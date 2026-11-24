"""Cuts one channel into speech segments at pauses (30 ms VAD frames), and a long stretch of speech every 8 s."""
from __future__ import annotations

from collections import deque
from typing import Callable, Iterator

from callcoach.config import RATE, SAMPLE_BYTES
from callcoach.data import Segment

FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * SAMPLE_BYTES
FRAME_S = FRAME_MS / 1000


class Segmenter:
    def __init__(self, channel: str, is_speech: Callable[[bytes], bool], ids: Iterator[int], *,
                 silence_ms: int = 600, max_seconds: float = 8.0, min_voiced_ms: int = 240,
                 preroll_ms: int = 210, tail_ms: int = 210):
        self.channel = channel
        self.is_speech = is_speech
        self.ids = ids
        self.silence_frames = silence_ms // FRAME_MS
        self.max_frames = int(max_seconds * 1000) // FRAME_MS
        self.min_voiced = min_voiced_ms // FRAME_MS
        self.tail_frames = tail_ms // FRAME_MS
        self.preroll: deque[tuple[bytes, float]] = deque(maxlen=preroll_ms // FRAME_MS)
        self.buffer = b""
        self.heard_at: float | None = None
        self._reset()

    def _reset(self) -> None:
        self.frames: list[bytes] = []
        self.t_start = 0.0
        self.voiced = 0
        self.silence = 0
        self.last_voiced_index = -1
        self.last_voiced_t = 0.0

    @property
    def active(self) -> bool:
        return bool(self.frames)

    @property
    def spoken_seconds(self) -> float:
        """How long the segment that is still open is, up to its last speech: a pause before it closes adds
        nothing."""
        return (self.last_voiced_index + 1) * FRAME_S

    def open_audio(self, seconds: float) -> bytes:
        """The last seconds of the segment that is still open, for a partial transcription."""
        n = int(seconds / FRAME_S)
        return b"".join(self.frames[-n:]) if n > 0 else b""

    def feed(self, pcm: bytes, t_end: float) -> list[Segment]:
        data = self.buffer + pcm
        usable = len(data) - len(data) % FRAME_BYTES
        self.buffer = data[usable:]
        n = usable // FRAME_BYTES
        leftover_s = len(self.buffer) / (RATE * SAMPLE_BYTES)
        out = []
        for i in range(n):
            t = t_end - leftover_s - (n - 1 - i) * FRAME_S
            seg = self._frame(data[i * FRAME_BYTES:(i + 1) * FRAME_BYTES], t)
            if seg:
                out.append(seg)
        return out

    def flush(self, t: float) -> Segment | None:
        return self._close(t, trim=True) if self.active else None

    def discard(self) -> None:
        """Drops the segment still open, the audio kept before it and when speech was last heard, so nothing fed
        so far reaches a later segment."""
        self._reset()
        self.preroll.clear()
        self.buffer = b""
        self.heard_at = None

    def _frame(self, frame: bytes, t: float) -> Segment | None:
        voiced = self.is_speech(frame)
        if not self.active:
            if not voiced:
                self.preroll.append((frame, t))
                return None
            self.frames = [f for f, _ in self.preroll]
            self.t_start = (self.preroll[0][1] if self.preroll else t) - FRAME_S
            self.preroll.clear()
        self.frames.append(frame)
        if voiced:
            self.voiced += 1
            self.silence = 0
            self.last_voiced_index = len(self.frames) - 1
            self.last_voiced_t = t
            self.heard_at = t
        else:
            self.silence += 1
        if self.silence >= self.silence_frames:
            return self._close(t, trim=True)
        if len(self.frames) >= self.max_frames:
            return self._close(t, trim=False)
        return None

    def _close(self, t: float, trim: bool) -> Segment | None:
        frames = self.frames[:self.last_voiced_index + 1 + self.tail_frames] if trim else self.frames
        seg = None
        if self.voiced >= self.min_voiced:
            seg = Segment(next(self.ids), self.channel, b"".join(frames), self.t_start, self.last_voiced_t, t)
        self._reset()
        return seg
