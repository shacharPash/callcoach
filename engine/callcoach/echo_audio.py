"""Speaker echo on the mic, judged from audio: the mic's energy envelope follows the other side's, at a clearly
lower level (sound from the speakers reaches the mic attenuated). Segments under about a second are not judged."""
from __future__ import annotations

import threading

import numpy as np

from callcoach.config import RATE, SAMPLE_BYTES

FRAME = RATE // 50
FLOOR_DB = -60.0
MIN_FRAMES = 50
MIN_SPAN_DB = 6.0
MIN_ATTENUATION_DB = 10.0
LEVEL_PERCENTILE = 90
LEAD_FRAMES = 5
LAG_FRAMES = 15


def envelope(pcm: bytes) -> np.ndarray:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0)
    frames = x[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return np.maximum(20.0 * np.log10(rms), FLOOR_DB)


def echo_score(mic: bytes, them: bytes) -> float:
    a, b = envelope(mic), envelope(them)
    n = min(len(a), len(b))
    if n < MIN_FRAMES or np.ptp(b[:n]) < MIN_SPAN_DB:
        return 0.0
    a, b = a[:n], b[:n]
    if np.percentile(b, LEVEL_PERCENTILE) - np.percentile(a, LEVEL_PERCENTILE) < MIN_ATTENUATION_DB:
        return 0.0
    best = 0.0
    for lag in range(-LEAD_FRAMES, LAG_FRAMES + 1):
        x, y = (a[lag:], b[: n - lag]) if lag >= 0 else (a[: n + lag], b[-lag:])
        if len(x) < MIN_FRAMES or x.std() < 1e-6 or y.std() < 1e-6:
            continue
        best = max(best, float(np.corrcoef(x, y)[0, 1]))
    return best


class AudioHistory:
    """The last seconds of one channel, aligned to the wall clock like the recording (gaps are silence)."""

    def __init__(self, seconds: float = 30.0, gap_tolerance_s: float = 0.25):
        self.max_bytes = round(seconds * RATE) * SAMPLE_BYTES
        self.gap_tolerance = round(gap_tolerance_s * RATE)
        self.buf = bytearray()
        self.end: float | None = None
        self._lock = threading.Lock()

    def add(self, pcm: bytes, t_end: float) -> None:
        with self._lock:
            if self.end is not None:
                gap = round((t_end - self.end) * RATE) - len(pcm) // SAMPLE_BYTES
                if gap > self.gap_tolerance:
                    self.buf += bytes(gap * SAMPLE_BYTES)
            self.buf += pcm
            if len(self.buf) > 2 * self.max_bytes:
                del self.buf[: len(self.buf) - self.max_bytes]
            self.end = t_end

    def slice(self, t0: float, t1: float) -> bytes:
        n = max(0, round((t1 - t0) * RATE))
        out = bytearray(n * SAMPLE_BYTES)
        with self._lock:
            if self.end is None:
                return bytes(out)
            total = len(self.buf) // SAMPLE_BYTES
            stop = total - round((self.end - t1) * RATE)
            start = stop - n
            lo, hi = max(start, 0), min(stop, total)
            if hi > lo:
                out[(lo - start) * SAMPLE_BYTES:(hi - start) * SAMPLE_BYTES] = \
                    self.buf[lo * SAMPLE_BYTES:hi * SAMPLE_BYTES]
        return bytes(out)
