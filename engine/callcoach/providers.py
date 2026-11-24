"""Shared provider types and a scripted fake. A Provider is the only thing that talks to an AI service."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from callcoach.prompts import RECAP_RULES


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input + other.input, self.output + other.output, self.cache_read + other.cache_read,
                     self.cache_write + other.cache_write, self.requests + other.requests)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_api(cls, usage) -> "Usage":
        def get(key: str) -> int:
            value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            return int(value or 0)

        return cls(get("input_tokens"), get("output_tokens"), get("cache_read_input_tokens"),
                   get("cache_creation_input_tokens"), 1)


@dataclass(frozen=True)
class Result:
    data: object
    usage: Usage
    model: str


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderIncomplete(ProviderUnavailable):
    """The model stopped before its answer was done: cut at max_tokens, refused, or another stop reason."""


class ProviderAuth(ProviderError):
    pass


class NoteBuffer:
    """Diagnostics a provider keeps for its caller's log, since a provider has no emitter."""

    def __init__(self):
        self._notes: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def add(self, kind: str, **fields) -> None:
        with self._lock:
            self._notes.append((kind, fields))

    def take(self) -> list[tuple[str, dict]]:
        with self._lock:
            notes, self._notes = self._notes, []
        return notes


def drain_notes(provider, emitter) -> None:
    """Writes the provider's pending notes to the emitter's log."""
    take = getattr(provider, "take_notes", None)
    for kind, fields in take() if take else []:
        emitter.note(kind, **fields)


class Provider(Protocol):
    name: str

    def open_call(self, system: str, schema: dict) -> None: ...

    def step(self, message: str) -> Result: ...

    def prepare(self, system: str, message: str, schema: dict) -> Result: ...

    def summarize(self, system: str, message: str) -> Result: ...

    def explain(self, system: str, message: str) -> Result: ...

    def spot(self, system: str, message: str) -> Result: ...

    def translate(self, system: str, message: str) -> Result: ...

    def look(self, system: str, message: str, image: bytes, media_type: str) -> Result: ...

    def recap(self, system: str, message: str) -> Result: ...

    def research(self, system: str, message: str) -> Result: ...

    def brief(self, system: str, message: str) -> Result: ...

    def followup(self, system: str, message: str) -> Result: ...

    def check(self) -> Result: ...

    def close(self) -> None: ...

    def take_notes(self) -> list[tuple[str, dict]]: ...


DEMO_CARDS = (
    (("הייתי משתמש",), {"kind": "tip", "text": "מחמאה. תבקש דוגמה אמיתית", "lang": "he", "he": ""}),
    (("המשחק",), {"kind": "steer", "text": "חזרה: מי מחליט על רכישה?", "lang": "he", "he": ""}),
    (("Nimbus",), {"kind": "question", "text": "What would make you switch?", "lang": "en",
                   "he": "מה יגרום לכם לעבור?"}),
)
DEMO_TERMS = (
    (("SOC 2", "SOC2", "שק 2", "שק שתיים"), {"term": "SOC 2", "note": "תקן ביקורת אבטחה לספקי שירותים",
                                             "kind": "term"}),
    (("Okta",), {"term": "Okta", "note": "שירות התחברות וניהול זהויות לארגונים", "kind": "product"}),
    (("Nimbus",), {"term": "Nimbus Labs", "note": "כלי שקנו בשנה שעברה (מההערות)", "kind": "company"}),
)
DEMO_QUESTIONS = (
    (("בלגן", "בלאגן"), [{"text": "מתי זה יצר לכם בעיה?", "he": ""}, {"text": "מי מאשר בקשה כזאת?", "he": ""}]),
    (("יומיים", "יומאיים"), [{"text": "כמה זה עלה לכם?", "he": ""}, {"text": "מה עשיתם בינתיים?", "he": ""}]),
    (("Nimbus",), [{"text": "Why does nobody use it?", "he": "למה אף אחד לא משתמש בו?"},
                   {"text": "Who picked Nimbus Labs?", "he": "מי בחר ב-Nimbus Labs?"}]),
)
DEMO_ASKED = ((("How is yours different",), {"text": "How is yours different?", "he": "במה שלכם שונה?"}),)
DEMO_NOW = (
    (("בלגן", "בלאגן"), "דנה מסבירה שבקשות גישה מגיעות היום בסלאק ומאושרות ידנית"),
    (("יומיים", "יומאיים"), "דנה מספרת על מפתח שחיכה יומיים לגישה"),
    (("Nimbus",), "מדברים על Nimbus Labs, כלי שקנו ואף אחד לא משתמש בו"),
)
DEMO_GOALS = (("בלגן", "g1"), ("בלאגן", "g1"), ("יומיים", "g2"), ("יומאיים", "g2"), ("מחליט", "g3"))
FORCED_CARD = {"kind": "question", "text": "מה הייתם עושים אילו זה היה נפתר מחר?", "lang": "he", "he": ""}


def _found(table, new: str, default):
    return next((value for needles, value in table if any(n in new for n in needles)), default)


def demo_reply(message: str) -> dict:
    """A scripted live answer to the demo call's planted moments, leaving closed sections empty."""
    new, _, state = message.partition("## State")
    ask = "ask off" not in state
    goals = [{"id": g, "done": True} for needle, g in DEMO_GOALS if needle in new]
    terms = [term for needles, term in DEMO_TERMS if any(n in new for n in needles)] if "terms off" not in state else []
    questions = _found(DEMO_QUESTIONS, new, []) if ask and "live_questions: on" in state else []
    asked = _found(DEMO_ASKED, new, None) if ask and "hearing_me: off" not in state else None
    now = _found(DEMO_NOW, new, "") if "now on" in state else ""
    base = {"goals": goals, "terms": terms, "questions": questions, "now": now, "asked": asked}
    if "mode: forced_question" in state and ask:
        return {"card": FORCED_CARD, **base}
    if "mode: goals_only" in state or "level: quiet" in state or not ask:
        return {"card": None, **base}
    for needles, card in DEMO_CARDS:
        if any(n in new for n in needles):
            return {"card": card, **base}
    return {"card": None, **base}


DEMO_PREP = {"goals": ["להבין איך מנהלים היום בקשות גישה", "למצוא את הפעם האחרונה שזה כאב, עם דוגמה",
                       "להבין מי מחליט על רכישה"],
             "questions": [{"text": "איך אתם מנהלים היום בקשות גישה לסביבות הענן?", "he": ""},
                           {"text": "מתי בפעם האחרונה זה יצר לכם בעיה?", "he": ""},
                           {"text": "מי מאשר בקשה כזאת?", "he": ""}],
             "glossary": ["SOC 2", "Okta", "Nimbus Labs"]}
DEMO_DETAIL = ("שירות התחברות וניהול זהויות לארגונים, שמנהל מי נכנס לאילו מערכות. "
               "הוא עלה כי הצד השני סיפר שבקשות הגישה אצלם עוברות דרכו.")
DEMO_SPOTTED = '{"terms": [{"term": "Okta", "note": "שירות התחברות וניהול זהויות", "kind": "product"}]}'
DEMO_SCREEN = ('{"screen": "שקף שמשווה שלוש חבילות של Okta, עם מחיר למשתמש בכל אחת", "terms": '
               '[{"term": "Okta Identity Governance", "note": "ניהול הרשאות וביקורות גישה", "kind": "product"}]}')
DEMO_RECAP = ("דנה מספרת שבקשות גישה מגיעות בסלאק ומאושרות ידנית\n"
              "בשבוע שעבר מפתח חיכה יומיים לגישה, והשחרור התעכב\n"
              "הם קנו את Nimbus Labs בשנה שעברה ואף אחד לא משתמש בו")
DEMO_FOLLOWUP = """Subject: תודה על השיחה היום

היי דנה,

תודה שמצאת זמן לשיחה היום.

על מה דיברנו:
- בקשות גישה מגיעות היום בסלאק ומאושרות ידנית
- הביקורת של SOC 2 נעשית מאקסל פעם ברבעון

הצעד הבא: אשלח הזמנה לפגישה ביום שלישי.

תודה,
"""
DEMO_RESEARCH = """## אנשים
- דנה: מנהלת IT ב-Acme (מחקר לדוגמה)
## חברה
- Acme: חברה לדוגמה שעובדת עם Okta
## מקורות
- https://example.com/acme
"""
DEMO_BRIEF = """---
title: שיחת היכרות עם Acme
type: validation
lang: he
length: 30
goals:
  - להבין איך מנהלים היום בקשות גישה
  - להבין מי מחליט על רכישה
questions:
  - איך אתם מנהלים היום בקשות גישה לענן?
glossary: Acme, Okta, SOC 2
terms:
  - SOC 2: תקן ביקורת אבטחה לספקי שירותים
  - Nimbus Labs: כלי שקנו בשנה שעברה
---
## מי בשיחה
- דנה: מנהלת IT ב-Acme (תדריך לדוגמה)
## רקע
- חברה לדוגמה שעובדת עם Okta
## לא לשכוח
- לבקש דוגמה אמיתית מהפעם האחרונה
"""


class FakeProvider:
    """Scripted replies for tests and app development: no network, no tokens."""

    name = "fake"
    model = "fake"

    def __init__(self, replies: Callable[[str], object] | list | None = None, *, delay: float = 0.0,
                 errors: list | None = None, sleep: Callable[[float], None] = time.sleep, prep: dict | None = None,
                 summary: str = "## מה למדתי\n- (סיכום לדוגמה)\n", detail: str = DEMO_DETAIL,
                 recap: str = DEMO_RECAP, research: str = DEMO_RESEARCH, brief: str = DEMO_BRIEF,
                 followup: str = DEMO_FOLLOWUP, spotted: str = DEMO_SPOTTED, screen: str = DEMO_SCREEN,
                 translated: str | None = None):
        self.replies = demo_reply if replies is None else replies
        self.delay = delay
        self.errors = list(errors or [])
        self.sleep = sleep
        self.prep_reply = prep or DEMO_PREP
        self.summary = summary
        self.detail = detail
        self.spotted = spotted
        self.screen = screen
        self.translated = translated
        self.images: list[tuple[bytes, str]] = []
        self.recap_reply = recap
        self.research_reply = research
        self.brief_reply = brief
        self.followup_reply = followup
        self.calls: list[tuple[str, str]] = []
        self.system: str | None = None
        self.closed = False
        self.notes = NoteBuffer()
        self._lock = threading.Lock()

    def _next(self, kind: str, message: str) -> None:
        with self._lock:
            self.calls.append((kind, message))
            error = self.errors.pop(0) if self.errors else None
        if self.delay:
            self.sleep(self.delay)
        if error:
            raise error

    def open_call(self, system: str, schema: dict) -> None:
        self.system = system

    def step(self, message: str) -> Result:
        self._next("step", message)
        if callable(self.replies):
            data = self.replies(message)
        else:
            with self._lock:
                data = self.replies.pop(0) if self.replies else {"card": None, "goals": [], "terms": []}
        return Result(data, Usage(100, 20, 900, 0, 1), self.model)

    def prepare(self, system: str, message: str, schema: dict) -> Result:
        self._next("prepare", message)
        return Result(self.prep_reply, Usage(300, 80, 0, 0, 1), self.model)

    def summarize(self, system: str, message: str) -> Result:
        self._next("summarize", message)
        return Result(self.summary, Usage(2000, 400, 0, 0, 1), self.model)

    def explain(self, system: str, message: str) -> Result:
        """A term's detail."""
        self._next("explain", message)
        return Result(self.detail, Usage(400, 60, 0, 0, 1), self.model)

    def spot(self, system: str, message: str) -> Result:
        """The terms said a moment ago."""
        self._next("spot", message)
        return Result(self.spotted, Usage(300, 40, 0, 0, 1), self.model)

    def translate(self, system: str, message: str) -> Result:
        """English lines in Hebrew: by default each line asked for, marked as a translation."""
        self._next("translate", message)
        if self.translated is not None:
            return Result(self.translated, Usage(200, 30, 0, 0, 1), self.model)
        asked = message.split("## Translate", 1)[-1]
        lines = [{"id": int(m.group(1)), "he": "בעברית: " + m.group(2)}
                 for m in re.finditer(r"^(\d+): (.*)$", asked, re.M)]
        return Result(json.dumps({"lines": lines}, ensure_ascii=False), Usage(200, 30, 0, 0, 1), self.model)

    def look(self, system: str, message: str, image: bytes, media_type: str) -> Result:
        """What a screenshot of the call shows."""
        self.images.append((image, media_type))
        self._next("look", message)
        return Result(self.screen, Usage(1800, 60, 0, 0, 1), self.model)

    def recap(self, system: str, message: str) -> Result:
        """The recap of the call so far."""
        self._next("recap", message)
        return Result(self.recap_reply, Usage(400, 60, 0, 0, 1), self.model)

    def research(self, system: str, message: str) -> Result:
        self._next("research", message)
        return Result(self.research_reply, Usage(3000, 500, 0, 0, 1), self.model)

    def brief(self, system: str, message: str) -> Result:
        self._next("brief", message)
        return Result(self.brief_reply, Usage(5000, 900, 0, 0, 1), self.model)

    def followup(self, system: str, message: str) -> Result:
        self._next("followup", message)
        return Result(self.followup_reply, Usage(4000, 300, 0, 0, 1), self.model)

    def check(self) -> Result:
        self._next("check", "")
        return Result("OK", Usage(10, 2, 0, 0, 1), self.model)

    def close(self) -> None:
        self.closed = True

    def take_notes(self) -> list[tuple[str, dict]]:
        return self.notes.take()
