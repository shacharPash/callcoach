import array
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def tone(seconds: float, value: int = 3000) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * round(RATE * seconds)


def quiet(seconds: float) -> bytes:
    return bytes(2 * round(RATE * seconds))


def peak(pcm: bytes) -> int:
    samples = array.array("h")
    samples.frombytes(pcm)
    return max((abs(x) for x in samples), default=0)


def loud(frame: bytes) -> bool:
    return peak(frame) > 500


def write_stereo(path: Path, left: bytes, right: bytes) -> None:
    n = max(len(left), len(right)) // 2
    pair = np.zeros((n, 2), dtype="<i2")
    a, b = np.frombuffer(left, dtype="<i2"), np.frombuffer(right, dtype="<i2")
    pair[: len(a), 0] = a
    pair[: len(b), 1] = b
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pair.tobytes())


def has_ffmpeg() -> bool:
    from callcoach.config import tool

    try:
        tool("ffmpeg")
        tool("ffprobe")
        return True
    except FileNotFoundError:
        return False


def speech_like(seconds: float, seed: int, level: float = 0.3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = round(RATE * seconds)
    env = np.zeros(n)
    t = 0
    while t < n:
        on, off = int(rng.uniform(0.08, 0.3) * RATE), int(rng.uniform(0.05, 0.25) * RATE)
        env[t:t + on] = rng.uniform(0.3, 1.0)
        t += on + off
    return rng.standard_normal(n) * env * level


def delayed(x: np.ndarray, ms: float, gain: float) -> np.ndarray:
    d = round(RATE * ms / 1000)
    return np.concatenate([np.zeros(d), x[:-d]]) * gain


def to_pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
