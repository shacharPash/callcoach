"""Drops Whisper hallucinations and mic lines that only repeat the other side (speaker echo)."""
from __future__ import annotations

import difflib
import re
from collections import deque

from callcoach.data import THEM, Line, Segment

HALLUCINATIONS = ("תודה רבה, אדוני היושב-ראש", "אדוני היושב-ראש", "חברי הכנסת",
                  "תודה שצפיתם", "Thanks for watching", "Subtitles by", "Thank you for watching",
                  "תודה על הצפייה")
_BRACKETED = re.compile(r"^\W*(?:[\[(][^\[\]()]*[\])]\W*)+$")
_WORDS = re.compile(r"\w+", re.UNICODE)


def clean(text: str, glossary: tuple[str, ...] = ()) -> str | None:
    text = " ".join(text.split())
    if not _WORDS.search(text) or _BRACKETED.match(text):
        return None
    key = text.strip(" .,!?").lower()
    if len(key.split()) <= 8 and any(key.startswith(h.lower()) for h in HALLUCINATIONS):
        return None
    if glossary and _repeats_glossary(set(_WORDS.findall(key)), glossary):
        return None
    return text


def _repeats_glossary(words: set[str], glossary: tuple[str, ...]) -> bool:
    """Whisper can echo its prompt, the glossary, as a line of its own: text made only of glossary words that
    names two glossary items or more. One item said alone ("Nimbus Labs.") is a real answer and is kept."""
    known = {w for term in glossary for w in _WORDS.findall(term.lower())}
    if not words or not words <= known:
        return False
    named = {frozenset(_WORDS.findall(term.lower())) for term in glossary}
    named = {t for t in named if t and t <= words}
    return len({t for t in named if not any(t < other for other in named)}) >= 2


def similarity(a: str, b: str) -> float:
    na, nb = " ".join(_WORDS.findall(a.lower())), " ".join(_WORDS.findall(b.lower()))
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio() if na and nb else 0.0


class EchoFilter:
    """A mic line is held until the other side's audio has been segmented past it (close_s after its
    speech end) and no overlapping 'them' segment is still being transcribed, for at most max_hold_s.

    Not thread safe: the engine calls every method under one lock and calls done() for every expected
    segment that gives no line. Lines are not returned in time order; consumers place them by t_start.
    """

    def __init__(self, window_s: float = 2.0, threshold: float = 0.8, max_hold_s: float = 3.0,
                 close_s: float = 0.7):
        self.window_s = window_s
        self.threshold = threshold
        self.max_hold_s = max_hold_s
        self.close_s = close_s
        self.them_clock = float("-inf")
        self.pending_them: dict[int, float] = {}
        self.recent_them: deque[Line] = deque(maxlen=20)
        self.held: list[tuple[Line, float]] = []

    def advance(self, t: float) -> None:
        self.them_clock = max(self.them_clock, t)

    def expect(self, seg: Segment) -> None:
        if seg.channel == THEM:
            self.pending_them[seg.id] = seg.speech_end

    def done(self, segment_id: int) -> None:
        self.pending_them.pop(segment_id, None)

    def add(self, line: Line, now: float) -> list[Line]:
        if line.channel == THEM:
            self.done(line.segment_id)
            self.recent_them.append(line)
            return [line] + self._release(now)
        if self._is_echo(line):
            return []
        if self._waits(line):
            self.held.append((line, now))
            return []
        return [line]

    def tick(self, now: float) -> list[Line]:
        return self._release(now)

    def _release(self, now: float) -> list[Line]:
        out, keep = [], []
        for line, since in self.held:
            if self._is_echo(line):
                continue
            if self._waits(line) and now - since < self.max_hold_s:
                keep.append((line, since))
            else:
                out.append(line)
        self.held = keep
        return out

    def _waits(self, line: Line) -> bool:
        if self.them_clock < line.speech_end + self.close_s:
            return True
        return any(abs(t - line.speech_end) <= self.window_s for t in self.pending_them.values())

    def _is_echo(self, line: Line) -> bool:
        return any(abs(o.speech_end - line.speech_end) <= self.window_s
                   and similarity(o.text, line.text) >= self.threshold for o in self.recent_them)
