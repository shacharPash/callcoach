"""Removes the other side's voice from my microphone when it comes back from the speakers, before anything records,
transcribes or measures the mic. WebRTC's echo canceller (AEC3, through livekit) learns how the speakers sound on
the mic from the other side's audio (the far end) and subtracts it; my own voice passes untouched.

The other side's chunks pass straight on. Each 10 ms of the mic is processed once the other side's audio up to its
time has gone in first (the echo always comes after the sound that made it), or after max_wait_s without it, and
leaves as the same chunks, with the same lengths and times. Both sides are placed in time by counting their samples,
like the recorder's tracks, not by when each chunk arrived: a chunk held back by a busy thread would otherwise put
the far end out of line with the mic, and the canceller would lose the echo. On headphones the mic holds no echo, and
the canceller would only make my voice quieter where I talk over the other side while it learns, so there the mic
goes on as it came. Apple's echo cancellation is not used: while it runs on speakers, every other app gets the mic
almost silent."""
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


def headphones_now() -> bool | None:
    """Whether the sound goes to headphones now, by callcoach-mic's rule: Bluetooth (far more often headphones than a
    speaker) and the built-in headphone jack count; USB, HDMI, AirPlay and anything unknown may be speakers. None
    when Core Audio cannot tell."""
    try:
        from catap.bindings._coreaudio import (get_property_bytes, kAudioObjectPropertyScopeOutput,
                                               kAudioObjectSystemObject)

        def number(obj: int, key: bytes, **scope) -> int:
            return int.from_bytes(get_property_bytes(obj, int.from_bytes(key, "big"), **scope)[:4], "little")

        device = number(kAudioObjectSystemObject, b"dOut")
        transport = number(device, b"tran").to_bytes(4, "big")
        if transport == b"bltn":
            return number(device, b"ssrc", scope=kAudioObjectPropertyScopeOutput).to_bytes(4, "big") == b"hdpn"
        return transport in (b"blue", b"blea")
    except Exception:
        return None


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


class StreamClock:
    """Where one stream's samples sit in time, counted from its chunks. A chunk is stamped when it arrives, which is
    only ever late, so the stream's start is the earliest one the chunks of the last window_s give, and a chunk held
    back and then delivered in a burst moves nothing. Samples that never came, a gap longer than gap_tolerance_s,
    are counted as silence."""

    def __init__(self, window_s: float = 10.0, gap_tolerance_s: float = 0.25):
        self.window = window_s
        self.gap_tolerance = round(gap_tolerance_s * RATE)
        self.count = 0
        self._starts: deque[tuple[float, float]] = deque()

    @property
    def start(self) -> float | None:
        return self._starts[0][1] if self._starts else None

    def gap(self, samples: int, t_end: float) -> int:
        """How many samples of silence go before a chunk that arrived at t_end: none unless it is later than its
        count says by more than gap_tolerance_s."""
        if not self._starts:
            return 0
        gap = round((t_end - self.start) * RATE) - self.count - samples
        return gap if gap > self.gap_tolerance else 0

    def add(self, samples: int, t_end: float) -> int:
        """Counts a chunk that arrived at t_end, with the silence before it; returns that silence."""
        gap = self.gap(samples, t_end)
        self.count += gap + samples
        start = t_end - self.count / RATE
        while self._starts and self._starts[-1][1] >= start:
            self._starts.pop()
        self._starts.append((t_end, start))
        while self._starts[0][0] < t_end - self.window:
            self._starts.popleft()
        return gap

    def time_of(self, index: int) -> float:
        return self.start + index / RATE

    def index_at(self, t: float) -> int:
        return round((t - self.start) * RATE)


class EchoCanceller:
    """One call's echo canceller between the sources and the engine. put() takes both channels, from both source
    threads; the engine's sink gets them in order per channel. A canceller that fails sends the mic on as it is.
    headphones() is asked once for every check_s of the mic; while it says yes, the mic still goes through the
    canceller, so it keeps up with the call, but leaves as it came."""

    def __init__(self, apm, *, max_wait_s: float = 0.3, keep_s: float = 0.5, buffer_s: float = 5.0,
                 gap_tolerance_s: float = 0.25, window_s: float = 10.0,
                 on_error: Callable[[str], None] = lambda m: None,
                 headphones: Callable[[], bool | None] = lambda: False, check_s: float = 1.0,
                 on_headphones: Callable[[bool], None] = lambda on: None):
        self.apm = apm
        self.max_wait = round(max_wait_s * RATE)
        self.keep = round(keep_s * RATE)
        self.buffer_bytes = round(buffer_s * RATE) // FRAME * FRAME_BYTES
        self.on_error = on_error
        self.headphones = headphones
        self.check_every = round(check_s * RATE)
        self.on_headphones = on_headphones
        self._next_check = 0
        self._as_it_came = False
        self._lock = threading.Lock()
        self._sink: Callable[[Chunk], None] | None = None
        self._them = StreamClock(window_s, gap_tolerance_s)
        self._me = StreamClock(window_s, gap_tolerance_s)
        self._render = bytearray()
        self._mic = bytearray()
        self._out = bytearray()
        self._pending: deque[tuple[int, float]] = deque()
        self._failed = False

    def attach(self, sink: Callable[[Chunk], None]) -> None:
        with self._lock:
            self._sink = sink

    def put(self, chunk: Chunk) -> None:
        with self._lock:
            samples = len(chunk.pcm) // SAMPLE_BYTES
            if chunk.channel == THEM:
                self._sink(chunk)
                silence = min(self._them.add(samples, chunk.t_end) * SAMPLE_BYTES, self.buffer_bytes)
                self._render += bytes(silence) + chunk.pcm
                if len(self._render) > self.buffer_bytes:
                    del self._render[:len(self._render) - self.buffer_bytes]
            else:
                if self._me.gap(samples, chunk.t_end):
                    self._drain(force=True)
                self._me.add(samples, chunk.t_end)
                if self._me.count >= self._next_check:
                    self._next_check = self._me.count + self.check_every
                    self._check_output()
                self._mic += chunk.pcm
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
                end = self._frame_end()
                self._mic += bytes(FRAME_BYTES - rest)
                self._out += self._capture_frame(end)[:rest]
                self._mic.clear()
            self._emit()

    def _check_output(self) -> None:
        try:
            on = self.headphones() is True
        except Exception:
            on = False
        if on != self._as_it_came:
            self._as_it_came = on
            self.on_headphones(on)

    def _frame_end(self) -> int:
        """The mic's count at the end of the first frame waiting."""
        return self._me.count - len(self._mic) // SAMPLE_BYTES + FRAME

    def _far_end_at(self, end: int) -> int:
        """The far end's count at the moment the mic's count reaches end."""
        return self._them.index_at(self._me.time_of(end))

    def _ready(self, end: int) -> bool:
        if self._me.count - end >= self.max_wait:
            return True
        return self._them.start is not None and self._them.count >= self._far_end_at(end)

    def _drain(self, force: bool) -> None:
        while len(self._mic) >= FRAME_BYTES:
            end = self._frame_end()
            if not force and not self._ready(end):
                break
            self._out += self._capture_frame(end)
            del self._mic[:FRAME_BYTES]
        self._emit()

    def _capture_frame(self, end: int) -> bytes:
        frame = bytes(self._mic[:FRAME_BYTES])
        if self._failed:
            return frame
        try:
            self._feed_render(end)
            clean = self.apm.capture(frame)
            return frame if self._as_it_came else clean
        except Exception as e:
            self._failed = True
            self.on_error(f"echo cancellation stopped ({type(e).__name__}: {e}); the mic goes on as it is")
            return frame

    def _feed_render(self, end: int) -> None:
        """The other side's audio up to the mic frame's moment goes in, frame by frame, each once; what is older than
        keep_s before it is skipped (the mic stalled, or the call just started)."""
        if self._them.start is None:
            return
        target = min(self._far_end_at(end), self._them.count)
        fed = self._them.count - len(self._render) // SAMPLE_BYTES
        while fed + FRAME <= target:
            frame = bytes(self._render[:FRAME_BYTES])
            del self._render[:FRAME_BYTES]
            fed += FRAME
            if fed > target - self.keep:
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
                make_apm: Callable[[], object] = LivekitApm,
                headphones: Callable[[], bool | None] = headphones_now) -> list:
    """The live sources with the echo canceller between them and the engine; the sources as they are when it is
    turned off or cannot load. events.log notes each change between headphones and speakers."""
    if not aec_wanted(environ):
        on_note("aec", state="off")
        return sources
    try:
        apm = make_apm()
    except Exception as e:
        on_note("aec", state="unavailable", error=f"{type(e).__name__}: {e}")
        return sources
    canceller = EchoCanceller(apm, on_error=lambda message: on_note("aec", state="stopped", error=message),
                              headphones=headphones,
                              on_headphones=lambda on: on_note("aec", state="headphones" if on else "on"))
    on_note("aec", state="on")
    return [Cancelled(s, canceller) for s in sources]
