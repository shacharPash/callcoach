"""Talk share over the last minutes, and the local talk-too-much tip (no tokens), always in Hebrew like every tip."""
from __future__ import annotations

from collections import deque

from callcoach.data import ME

TIP = "חלק הדיבור שלך גבוה. כדאי לשאול שאלה פתוחה ולתת להם לדבר."


class Meter:
    def __init__(self, window_s: float = 180.0, limit: float = 0.65, min_elapsed_s: float = 120.0,
                 every_s: float = 300.0, min_voiced_s: float = 30.0):
        self.window_s = window_s
        self.limit = limit
        self.min_elapsed_s = min_elapsed_s
        self.every_s = every_s
        self.min_voiced_s = min_voiced_s
        self.segments: deque[tuple[float, str, float]] = deque()
        self.last_tip: float | None = None

    def add(self, channel: str, duration: float, t: float) -> None:
        self.segments.append((t, channel, duration))

    def share(self, now: float) -> float | None:
        while self.segments and self.segments[0][0] < now - self.window_s:
            self.segments.popleft()
        total = sum(d for _, _, d in self.segments)
        if total < self.min_voiced_s:
            return None
        return sum(d for _, c, d in self.segments if c == ME) / total

    def tip(self, now: float, started: float) -> str | None:
        if now - started < self.min_elapsed_s:
            return None
        if self.last_tip is not None and now - self.last_tip < self.every_s:
            return None
        share = self.share(now)
        if share is None or share < self.limit:
            return None
        self.last_tip = now
        return TIP
