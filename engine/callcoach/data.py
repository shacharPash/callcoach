from __future__ import annotations

from dataclasses import asdict, dataclass

from callcoach.config import BYTES_PER_SECOND

ME = "me"
THEM = "them"
CHANNELS = (ME, THEM)


@dataclass(frozen=True)
class Chunk:
    channel: str
    pcm: bytes
    t_end: float


@dataclass(frozen=True)
class Segment:
    id: int
    channel: str
    pcm: bytes
    t_start: float
    speech_end: float
    closed_at: float

    @property
    def duration(self) -> float:
        return len(self.pcm) / BYTES_PER_SECOND


@dataclass(frozen=True)
class Line:
    id: int
    segment_id: int
    channel: str
    lang: str
    text: str
    t_start: float
    speech_end: float
    line_ready: float

    def to_dict(self) -> dict:
        return asdict(self)
