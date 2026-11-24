"""Which model transcribes a segment, and which language the other side is speaking."""
from __future__ import annotations

import threading


class LanguageRouter:
    def __init__(self, start: str, *, min_seconds: float = 1.5, threshold: float = 0.7, min_mass: float = 0.3,
                 min_english: float = 0.6):
        self.current = start
        self.locked = False
        self.min_seconds = min_seconds
        self.threshold = threshold
        self.min_mass = min_mass
        self.min_english = min_english
        self._lock = threading.Lock()

    def needs_detection(self, duration: float) -> bool:
        return not self.locked and duration >= self.min_seconds

    def detected(self, duration: float, probs: dict[str, float] | None) -> str | None:
        if not probs or duration < self.min_seconds:
            return None
        he, en = probs.get("he", 0.0), probs.get("en", 0.0)
        if he + en < self.min_mass:
            return None
        if he >= self.threshold * (he + en):
            return "he"
        if en >= self.threshold * (he + en) and en >= self.min_english:
            return "en"
        return None

    def choose(self, duration: float, probs: dict[str, float] | None) -> tuple[str, str | None]:
        """The language for this segment and the clear detection behind it; changes nothing."""
        with self._lock:
            found = None if self.locked else self.detected(duration, probs)
            return found or self.current, found

    def commit(self, lang: str) -> None:
        """Switches the channel once a line in that language is published (ignored when locked)."""
        with self._lock:
            if not self.locked:
                self.current = lang

    def lock(self, lang: str) -> None:
        with self._lock:
            self.current = lang
            self.locked = True

    def unlock(self) -> None:
        """Detection decides again, from the language the channel has now."""
        with self._lock:
            self.locked = False


class ConversationLanguage:
    def __init__(self, start: str, needed: int = 2):
        self.current = start
        self.needed = needed
        self.locked = False
        self._streak = 0
        self._lock = threading.Lock()

    def observe(self, lang: str) -> str:
        with self._lock:
            if self.locked or lang == self.current:
                self._streak = 0
                return self.current
            self._streak += 1
            if self._streak >= self.needed:
                self.current = lang
                self._streak = 0
            return self.current

    def lock(self, lang: str) -> None:
        with self._lock:
            self.current = lang
            self.locked = True
            self._streak = 0

    def unlock(self) -> None:
        with self._lock:
            self.locked = False
            self._streak = 0
