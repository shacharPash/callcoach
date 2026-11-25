"""Speaker echo on the mic, judged from audio: the mic's energy envelope follows the other side's, at a clearly
lower level (sound from the speakers reaches the mic attenuated). Segments under about a second are not judged.
What the echo canceller leaves of the echo is caught by its level (LevelGate)."""
from __future__ import annotations

import threading
from collections import deque

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
GATE_MARGIN_DB = 12.0
GATE_FALLBACK_DB = -50.0
THEM_ACTIVE_DB = -45.0
LEARN_FRAMES = 25
LEARN_KEEP = 15
PEAK_FRAMES = 15


def envelope(pcm: bytes) -> np.ndarray:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0)
    frames = x[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return np.maximum(20.0 * np.log10(rms), FLOOR_DB)


def level(pcm: bytes) -> float:
    """The level of the loud part of the audio, in dBFS."""
    env = envelope(pcm)
    return float(np.percentile(env, LEVEL_PERCENTILE)) if len(env) else FLOOR_DB


def peak_level(pcm: bytes) -> float:
    """The level of the loudest 300 ms of the audio, in dBFS: a short word in a long quiet segment still counts."""
    env = envelope(pcm)
    if not len(env):
        return FLOOR_DB
    power = 10.0 ** (env / 10.0)
    k = min(PEAK_FRAMES, len(power))
    return float(10.0 * np.log10(np.convolve(power, np.ones(k) / k, mode="valid").max()))


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


class LevelGate:
    """What the echo canceller leaves of the echo: a sentence on the mic whose loudest 300 ms are much quieter than
    my voice, while the other side talks. My voice's level is the median of my sentences while the other side is
    quiet (half a second or more each); until there is one, a fixed limit. A sentence of mine said over the other
    side keeps its level, and so does a short word inside a long quiet segment, so they stay."""

    def __init__(self, margin_db: float = GATE_MARGIN_DB, fallback_db: float = GATE_FALLBACK_DB,
                 them_active_db: float = THEM_ACTIVE_DB):
        self.margin = margin_db
        self.fallback = fallback_db
        self.them_active = them_active_db
        self._levels: deque[float] = deque(maxlen=LEARN_KEEP)
        self._lock = threading.Lock()

    def limit(self) -> float:
        with self._lock:
            return float(np.median(self._levels)) - self.margin if self._levels else self.fallback

    def judge(self, mic: bytes, them: bytes) -> tuple[bool, float, float]:
        """Whether the mic sentence is left-over echo, with its level and the limit it was judged by."""
        mine = peak_level(mic)
        if level(them) < self.them_active:
            if len(mic) // SAMPLE_BYTES >= LEARN_FRAMES * FRAME:
                with self._lock:
                    self._levels.append(mine)
            return False, mine, self.limit()
        limit = self.limit()
        return mine < limit, mine, limit
