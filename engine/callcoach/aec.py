"""Removes the other side's voice from my microphone when it comes back from the speakers, before anything records,
transcribes or measures the mic. WebRTC's echo canceller (AEC3, through livekit) learns how the speakers sound on
the mic from the other side's audio (the far end) and subtracts it; my own voice passes untouched.

The other side's chunks pass straight on. Each 10 ms of the mic is processed once the other side's audio up to its
time has gone in first (the echo always comes after the sound that made it), or after max_wait_s without it, and
leaves as the same chunks, with the same lengths and times. Apple's echo cancellation is not used: while it runs on
speakers, every other app gets the mic almost silent."""
from __future__ import annotations

import os
import threading
from collections import deque
from typing import Callable

from callcoach.config import RATE, SAMPLE_BYTES
from callcoach.data import ME, THEM, Chunk

AEC_ENV = "CALLCOACH_AEC"
FRAME = RATE // 100
FRAME_BYTES = FRAME * SAMPLE_BYTES


class LivekitApm:
    """AEC3 alone: no noise suppression, gain control or high-pass filter, so my voice keeps its level."""

    def __init__(self):
        from livekit import rtc

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True)

    def render(self, frame: bytes) -> None:
        self._apm.process_reverse_stream(self._rtc.AudioFrame(bytearray(frame), RATE, 1, FRAME))

    def capture(self, frame: bytes) -> bytes:
        f = self._rtc.AudioFrame(bytearray(frame), RATE, 1, FRAME)
        self._apm.set_stream_delay_ms(0)
        self._apm.process_stream(f)
        return bytes(f.data)


class EchoCanceller:
    """One call's echo canceller between the sources and the engine. put() takes both channels, from both source
    threads; the engine's sink gets them in order per channel. A canceller that fails sends the mic on as it is."""

    def __init__(self, apm, *, max_wait_s: float = 0.3, keep_s: float = 0.5, buffer_s: float = 5.0,
                 gap_tolerance_s: float = 0.25, on_error: Callable[[str], None] = lambda m: None):
        self.apm = apm
        self.max_wait = max_wait_s
        self.keep = round(keep_s * RATE)
        self.buffer_bytes = round(buffer_s * RATE) // FRAME * FRAME_BYTES
        self.gap_tolerance = round(gap_tolerance_s * RATE)
        self.on_error = on_error
        self._lock = threading.Lock()
        self._sink: Callable[[Chunk], None] | None = None
        self._render = bytearray()
        self._fed = 0
        self._total = 0
        self._them_end: float | None = None
        self._mic = bytearray()
        self._me_end: float | None = None
        self._out = bytearray()
        self._pending: deque[tuple[int, float]] = deque()
        self._failed = False

    def attach(self, sink: Callable[[Chunk], None]) -> None:
        with self._lock:
            self._sink = sink

    def put(self, chunk: Chunk) -> None:
        with self._lock:
            if chunk.channel == THEM:
                self._sink(chunk)
                self._add_render(chunk)
            else:
                self._mic += chunk.pcm
                self._me_end = chunk.t_end
                self._pending.append((len(chunk.pcm), chunk.t_end))
            self._drain(force=False)

    def flush(self) -> None:
        """Sends on all of the mic still waiting, at the end of the call."""
        with self._lock:
            if self._sink is None:
                return
            self._drain(force=True)
            if self._mic:
                rest = len(self._mic)
                self._mic += bytes(FRAME_BYTES - rest)
                self._out += self._capture_frame(self._frame_end())[:rest]
                self._mic.clear()
            self._emit()

    def _add_render(self, chunk: Chunk) -> None:
        if self._them_end is not None:
            gap = round((chunk.t_end - self._them_end) * RATE) - len(chunk.pcm) // SAMPLE_BYTES
            if gap > self.gap_tolerance:
                self._render += bytes(gap * SAMPLE_BYTES)
                self._total += gap
        self._render += chunk.pcm
        self._total += len(chunk.pcm) // SAMPLE_BYTES
        self._them_end = chunk.t_end
        if len(self._render) > self.buffer_bytes:
            drop = -(-(len(self._render) - self.buffer_bytes) // FRAME_BYTES) * FRAME_BYTES
            del self._render[:drop]
            self._fed += drop // SAMPLE_BYTES

    def _frame_end(self) -> float:
        """The time of the last sample of the first mic frame waiting."""
        return self._me_end - (len(self._mic) // SAMPLE_BYTES - FRAME) / RATE

    def _ready(self, t1: float) -> bool:
        return (self._them_end is not None and self._them_end >= t1) or self._me_end - t1 >= self.max_wait

    def _drain(self, force: bool) -> None:
        while len(self._mic) >= FRAME_BYTES:
            t1 = self._frame_end()
            if not force and not self._ready(t1):
                break
            self._out += self._capture_frame(t1)
            del self._mic[:FRAME_BYTES]
        self._emit()

    def _capture_frame(self, t1: float) -> bytes:
        frame = bytes(self._mic[:FRAME_BYTES])
        if self._failed:
            return frame
        try:
            self._feed_render(t1)
            return self.apm.capture(frame)
        except Exception as e:
            self._failed = True
            self.on_error(f"echo cancellation stopped ({type(e).__name__}: {e}); the mic goes on as it is")
            return frame

    def _feed_render(self, t1: float) -> None:
        """The other side's audio up to t1 goes in, frame by frame, each once; what is older than keep_s before
        t1 is skipped (the mic stalled, or the call just started)."""
        if self._them_end is None:
            return
        target = min(self._total - round((self._them_end - t1) * RATE), self._total)
        while self._fed + FRAME <= target and len(self._render) >= FRAME_BYTES:
            frame = bytes(self._render[:FRAME_BYTES])
            del self._render[:FRAME_BYTES]
            self._fed += FRAME
            if self._fed > target - self.keep:
                self.apm.render(frame)

    def _emit(self) -> None:
        while self._pending and len(self._out) >= self._pending[0][0]:
            size, t_end = self._pending.popleft()
            pcm = bytes(self._out[:size])
            del self._out[:size]
            self._sink(Chunk(ME, pcm, t_end))


class Cancelled:
    """A source whose chunks go through the echo canceller on their way to the engine. Everything else is the
    source's own."""

    def __init__(self, source, canceller: EchoCanceller):
        self.source = source
        self.canceller = canceller

    @property
    def name(self) -> str:
        return type(self.source).__name__

    def start(self, sink: Callable[[Chunk], None]) -> None:
        self.canceller.attach(sink)
        self.source.start(self.canceller.put)

    def stop(self) -> None:
        try:
            self.source.stop()
        finally:
            self.canceller.flush()

    def __getattr__(self, name: str):
        return getattr(self.source, name)


def aec_wanted(environ=os.environ) -> bool:
    """On unless CALLCOACH_AEC=0."""
    return environ.get(AEC_ENV, "") != "0"


def cancel_echo(sources: list, on_note: Callable[..., None], *, environ=os.environ,
                make_apm: Callable[[], object] = LivekitApm) -> list:
    """The live sources with the echo canceller between them and the engine; the sources as they are when it is
    turned off or cannot load."""
    if not aec_wanted(environ):
        on_note("aec", state="off")
        return sources
    try:
        apm = make_apm()
    except Exception as e:
        on_note("aec", state="unavailable", error=f"{type(e).__name__}: {e}")
        return sources
    canceller = EchoCanceller(apm, on_error=lambda message: on_note("aec", state="stopped", error=message))
    on_note("aec", state="on")
    return [Cancelled(s, canceller) for s in sources]
