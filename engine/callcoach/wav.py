"""16 kHz 16 bit WAV files written as audio arrives, readable after a crash."""
from __future__ import annotations

import struct
from pathlib import Path

from callcoach.config import BYTES_PER_SECOND, RATE

HEADER_BYTES = 44


def header(data_bytes: int, channels: int = 1, rate: int = RATE) -> bytes:
    block = channels * 2
    return (
        b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16)
        + b"data" + struct.pack("<I", data_bytes)
    )


def pcm_to_wav(pcm: bytes) -> bytes:
    return header(len(pcm)) + pcm


def seconds(path: Path) -> float:
    return max(0, path.stat().st_size - HEADER_BYTES) / BYTES_PER_SECOND


def repair(path: Path) -> None:
    data = max(0, path.stat().st_size - HEADER_BYTES)
    data -= data % 2
    with open(path, "r+b") as f:
        f.truncate(HEADER_BYTES + data)
        f.seek(0)
        f.write(header(data))


class WavWriter:
    def __init__(self, path: Path, refresh_seconds: float = 5.0):
        self.path = path
        self._f = open(path, "wb")
        self._f.write(header(0))
        self._f.flush()
        self.data_bytes = 0
        self._refresh = int(refresh_seconds * BYTES_PER_SECOND)
        self._pending = 0

    @property
    def seconds(self) -> float:
        return self.data_bytes / BYTES_PER_SECOND

    def write(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise ValueError("pcm must be whole 16 bit samples")
        self._f.write(pcm)
        self._f.flush()
        self.data_bytes += len(pcm)
        self._pending += len(pcm)
        if self._pending >= self._refresh:
            self._patch()

    def close(self) -> None:
        if not self._f.closed:
            try:
                self._patch()
            finally:
                self._f.close()

    def _patch(self) -> None:
        self._f.seek(0)
        self._f.write(header(self.data_bytes))
        self._f.seek(0, 2)
        self._f.flush()
        self._pending = 0
