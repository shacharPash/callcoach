"""What the agent may answer: at most one card, new terms, live questions, the question asked of me, the line on
what is discussed now, goal updates, the goals reply, a term's detail and the recap of the call so far.

Questions (live, opening, a question card and the one asked of me) are in the call's language, each with a Hebrew
translation `he` that is "" when the text is Hebrew."""
from __future__ import annotations

import json
import re
import dataclasses
from dataclasses import dataclass

from callcoach.protocol import CARD_KINDS, LANGS

# A card is read in one glance during the call: a few words, except an answer to ask_now.
NUDGE_WORDS = 5
OFFERED_QUESTION_WORDS = 6
LONG_CARD_WORDS = 12
MAX_TEXT_CHARS = 120
MAX_TERMS = 5
MAX_TERM_CHARS = 40
MAX_NOTE_WORDS = 12
MAX_NOTE_CHARS = 90
MAX_SCREEN_WORDS = 30
MAX_QUESTIONS = 2
MAX_QUESTION_WORDS = 9
MAX_QUESTION_CHARS = 80
# A Hebrew translation of a question or a question card; the rules ask for at most 12 words.
MAX_HE_WORDS = 14
MAX_HE_CHARS = 110
# The question asked of me, as it was said, and its translation.
MAX_ASKED_WORDS = 16
MAX_ASKED_CHARS = 140
MAX_OPENING = 3
MAX_OPENING_WORDS = 16
MAX_OPENING_CHARS = 140
MAX_DETAIL_WORDS = 60
MAX_RECAP_LINES = 5
MAX_RECAP_WORDS = 70
MAX_SINCE_LINES = 3
MAX_SINCE_WORDS = 45
MAX_TRANSLATION_WORDS = 60
# The rules ask for at most 14 words; a line a little longer is still kept.
MAX_NOW_WORDS = 16
MAX_NOW_CHARS = 140
TERM_KINDS = ("person", "company", "product", "term")


def _translated_schema() -> dict:
    return {"type": "object", "properties": {"text": {"type": "string"}, "he": {"type": "string"}},
            "required": ["text", "he"], "additionalProperties": False}


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "card": {"anyOf": [{"type": "null"}, {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(CARD_KINDS)},
                           "text": {"type": "string"},
                           "lang": {"type": "string", "enum": list(LANGS)},
                           "he": {"type": "string"}},
            "required": ["kind", "text", "lang", "he"], "additionalProperties": False}]},
        "goals": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "done": {"type": "boolean"}},
            "required": ["id", "done"], "additionalProperties": False}},
        "terms": {"type": "array", "items": {
            "type": "object", "properties": {"term": {"type": "string"}, "note": {"type": "string"},
                                             "kind": {"type": "string", "enum": list(TERM_KINDS)}},
            "required": ["term", "note", "kind"], "additionalProperties": False}},
        "questions": {"type": "array", "items": _translated_schema()},
        "now": {"type": "string"},
        "asked": {"anyOf": [{"type": "null"}, _translated_schema()]},
    },
    "required": ["card", "goals", "terms", "questions", "now", "asked"],
    "additionalProperties": False,
}

PREP_SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": _translated_schema()},
        "glossary": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["goals", "questions", "glossary"],
    "additionalProperties": False,
}

_FENCE = re.compile(r"```(?:json)?")
_HEBREW = re.compile(r"[\u0590-\u05ff]")
_FOLD = re.compile(r"[\W_]+")
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u00b7#]+|\d+[.)])\s*")


class BadReply(ValueError):
    pass


@dataclass(frozen=True)
class Question:
    """A question in the call's language and its Hebrew translation ("" when the text is Hebrew)."""
    text: str
    he: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "he": self.he}


@dataclass(frozen=True)
class Card:
    kind: str
    text: str
    lang: str
    he: str = ""


@dataclass(frozen=True)
class Term:
    term: str
    note: str
    kind: str = "term"


@dataclass(frozen=True)
class Reply:
    card: Card | None
    goals: dict[str, bool]
    problems: tuple[str, ...] = ()
    terms: tuple[Term, ...] = ()
    questions: tuple[Question, ...] = ()
    asked: Question | None = None
    now: str = ""


@dataclass(frozen=True)
class PrepReply:
    goals: tuple[str, ...]
    questions: tuple[Question, ...]
    glossary: tuple[str, ...]


def load_json(data) -> dict:
    if isinstance(data, dict):
        return data
    if not isinstance(data, str):
        raise BadReply(f"reply is {type(data).__name__}, not JSON")
    text = _FENCE.sub("", data)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise BadReply("no JSON object in the reply")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise BadReply(f"reply is not JSON: {e}") from None
    if not isinstance(value, dict):
        raise BadReply("reply is not a JSON object")
    return value


def parse_reply(data, allowed: tuple[str, ...], forced: bool = False) -> Reply:
    obj = load_json(data)
    problems: list[str] = []
    goals: dict[str, bool] = {}
    raw_goals = obj.get("goals") or []
    if not isinstance(raw_goals, list):
        raise BadReply("goals is not a list")
    for g in raw_goals:
        if isinstance(g, dict) and isinstance(g.get("id"), str) and isinstance(g.get("done"), bool):
            goals[g["id"]] = g["done"]
        else:
            problems.append(f"bad goal {g!r}"[:120])
    card = None
    if obj.get("card") is not None:
        card, problem = _card(obj["card"], allowed, forced)
        if problem:
            problems.append(problem)
    terms = _terms(obj.get("terms"), problems)
    questions = _questions(obj.get("questions"), problems)
    now = _now(obj.get("now"), problems)
    asked = None
    if obj.get("asked") is not None:
        asked = translated(obj["asked"], MAX_ASKED_WORDS, MAX_ASKED_CHARS, "asked", problems,
                           he_limits=(MAX_ASKED_WORDS, MAX_ASKED_CHARS))
    return Reply(card, goals, tuple(problems), terms, questions, asked, now)


def word_limit(kind: str, forced: bool) -> int:
    if forced:
        return LONG_CARD_WORDS
    return OFFERED_QUESTION_WORDS if kind == "question" else NUDGE_WORDS


def hebrew(text: str) -> bool:
    return bool(_HEBREW.search(text))


def fold(text: str) -> str:
    """Text with case and punctuation folded, to tell whether two lines say the same."""
    return _FOLD.sub("", text.casefold())


def text_lang(texts) -> str:
    """he when every text is Hebrew, else en: the direction a list of questions is shown in."""
    texts = list(texts)
    return "he" if texts and all(hebrew(t) for t in texts) else "en" if texts else "he"


def question(text: str, he: str = "") -> Question:
    """A question with its translation, which is "" when the text is Hebrew or says the same."""
    return Question(text, "" if not he or hebrew(text) or fold(he) == fold(text) else he)


def questions_for_call(questions: tuple[Question, ...], lang: str) -> tuple[Question, ...]:
    """In a Hebrew call a question that came in another language shows its Hebrew translation: after a short English
    start the model at times goes on in the language of the questions already shown."""
    if lang != "he":
        return questions
    return tuple(Question(q.he) if q.he and not hebrew(q.text) else q for q in questions)


def card_for_call(card: Card, lang: str) -> Card:
    """A card in another language shows its Hebrew translation in a Hebrew call, as questions do."""
    if lang == "he" and card.he and not hebrew(card.text):
        return dataclasses.replace(card, text=card.he, lang="he", he="")
    return card


def _hebrew_of(text: str, he, limits: tuple[int, int], what: str, problems: list[str]) -> str:
    """The Hebrew translation of a text: "" when the text is Hebrew itself, the same text, or not a string; a
    translation over the limits is dropped and the text kept."""
    he = question(text, " ".join(he.split()) if isinstance(he, str) else "").he
    if not he:
        return ""
    if len(he.split()) > limits[0] or len(he) > limits[1]:
        problems.append(f"{what} translation is too long ({len(he.split())} words)")
        return ""
    return he


def translated(raw, max_words: int, max_chars: int, what: str, problems: list[str], *,
               he_limits: tuple[int, int] = (MAX_HE_WORDS, MAX_HE_CHARS)) -> Question | None:
    """A question as {text, he}; a plain string is a question without a translation."""
    text, he = raw, ""
    if isinstance(raw, dict):
        text, he = raw.get("text"), raw.get("he")
    if not isinstance(text, str) or not text.strip():
        problems.append(f"bad {what} {raw!r}"[:120])
        return None
    text = " ".join(text.split())
    if len(text.split()) > max_words or len(text) > max_chars:
        problems.append(f"{what} is too long ({len(text.split())} words)")
        return None
    return Question(text, _hebrew_of(text, he, he_limits, what, problems))


def _terms(raw, problems: list[str]) -> tuple[Term, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append("terms is not a list")
        return ()
    out: list[Term] = []
    for item in raw:
        term = item.get("term") if isinstance(item, dict) else None
        note = item.get("note") if isinstance(item, dict) else None
        if not isinstance(term, str) or not isinstance(note, str) or not term.strip() or not note.strip():
            problems.append(f"bad term {item!r}"[:120])
            continue
        term, note = " ".join(term.split()), " ".join(note.split())
        if len(term) > MAX_TERM_CHARS or len(note) > MAX_NOTE_CHARS or len(note.split()) > MAX_NOTE_WORDS:
            problems.append(f"term {term[:40]!r} is too long")
            continue
        kind = item.get("kind", "term")
        if kind not in TERM_KINDS:
            problems.append(f"term {term[:40]!r} has kind {kind!r}"[:120])
            kind = "term"
        out.append(Term(term, note, kind))
    if len(out) > MAX_TERMS:
        problems.append(f"{len(out)} terms, kept {MAX_TERMS}")
    return tuple(out[:MAX_TERMS])


def _questions(raw, problems: list[str]) -> tuple[Question, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append("questions is not a list")
        return ()
    found = (translated(item, MAX_QUESTION_WORDS, MAX_QUESTION_CHARS, "question", problems) for item in raw)
    out = [q for q in found if q is not None]
    if len(out) > MAX_QUESTIONS:
        problems.append(f"{len(out)} questions, kept {MAX_QUESTIONS}")
    return tuple(out[:MAX_QUESTIONS])


def _now(raw, problems: list[str]) -> str:
    """The line on what is discussed now; "" keeps the one shown, and a reply without it (the line is off) is fine."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        problems.append("now is not a string")
        return ""
    text = " ".join(raw.split())
    if len(text.split()) > MAX_NOW_WORDS or len(text) > MAX_NOW_CHARS:
        problems.append(f"now is too long ({len(text.split())} words)")
        return ""
    return text


def _card(raw, allowed: tuple[str, ...], forced: bool = False) -> tuple[Card | None, str | None]:
    if not isinstance(raw, dict):
        return None, "card is not an object"
    kind, text, lang = raw.get("kind"), raw.get("text"), raw.get("lang")
    if kind not in allowed:
        return None, f"card kind {kind!r} is not allowed here"
    if not isinstance(text, str) or not text.strip():
        return None, "card has no text"
    if lang not in LANGS:
        return None, f"card language {lang!r} is not supported"
    text = " ".join(text.split())
    if len(text.split()) > word_limit(kind, forced) or len(text) > MAX_TEXT_CHARS:
        return None, f"{kind} is too long ({len(text.split())} words)"
    problems: list[str] = []
    he = _hebrew_of(text, raw.get("he"), (MAX_HE_WORDS, MAX_HE_CHARS), kind, problems) if kind == "question" else ""
    return Card(kind, text, lang, he), (problems[0] if problems else None)


def parse_prep(data) -> PrepReply:
    obj = load_json(data)

    def strings(key: str, limit: int) -> tuple[str, ...]:
        values = obj.get(key) or []
        if not isinstance(values, list):
            raise BadReply(f"{key} is not a list")
        return tuple(" ".join(v.split()) for v in values if isinstance(v, str) and v.strip())[:limit]

    raw = obj.get("questions") or []
    if not isinstance(raw, list):
        raise BadReply("questions is not a list")
    found = (translated(q, MAX_OPENING_WORDS, MAX_OPENING_CHARS, "question", []) for q in raw)
    questions = tuple(q for q in found if q is not None)[:MAX_OPENING]
    return PrepReply(strings("goals", 5), questions, strings("glossary", 20))


def parse_spotted(data) -> tuple[tuple[Term, ...], tuple[str, ...]]:
    """The terms a search for what was just said found, newest first, checked as the live step's terms are, and
    what was wrong with the reply."""
    problems: list[str] = []
    return _terms(load_json(data).get("terms"), problems), tuple(problems)


def parse_screen(data) -> tuple[str, tuple[Term, ...], tuple[str, ...]]:
    """What a screenshot showed: one line cut to MAX_SCREEN_WORDS words, its terms checked as the live step's are,
    and what was wrong with the reply."""
    obj = load_json(data)
    problems: list[str] = []
    raw = obj.get("screen")
    if not isinstance(raw, str):
        problems.append("screen is not text")
    words = raw.split() if isinstance(raw, str) else []
    line = " ".join(words[:MAX_SCREEN_WORDS]) + ("…" if len(words) > MAX_SCREEN_WORDS else "")
    return line, _terms(obj.get("terms"), problems), tuple(problems)


def parse_translations(data, ids) -> tuple[tuple[tuple[int, str], ...], tuple[str, ...]]:
    """The Hebrew of the lines asked for, in the order they came, each cut to MAX_TRANSLATION_WORDS words, and what
    was wrong with the reply: a line not asked for, asked twice or with no text is left out."""
    problems: list[str] = []
    raw = load_json(data).get("lines")
    if not isinstance(raw, list):
        return (), ("lines is not a list",)
    found: dict[int, str] = {}
    for item in raw:
        line_id = item.get("id") if isinstance(item, dict) else None
        he = item.get("he") if isinstance(item, dict) else None
        if not isinstance(line_id, int) or line_id not in ids or line_id in found:
            problems.append(f"line {line_id!r} was not asked for")
            continue
        words = he.split() if isinstance(he, str) else []
        if not words:
            problems.append(f"line {line_id} has no translation")
            continue
        cut = " ".join(words[:MAX_TRANSLATION_WORDS])
        found[line_id] = cut + "…" if len(words) > MAX_TRANSLATION_WORDS else cut
    return tuple((i, found[i]) for i in ids if i in found), tuple(problems)


def parse_detail(data) -> str:
    """A term's explanation as one paragraph, cut to MAX_DETAIL_WORDS words."""
    words = data.split() if isinstance(data, str) else []
    text = " ".join(words[:MAX_DETAIL_WORDS])
    return text + "…" if len(words) > MAX_DETAIL_WORDS else text


def parse_recap(data, most_lines: int = MAX_RECAP_LINES, most_words: int = MAX_RECAP_WORDS) -> str:
    """The recap of the call so far: up to most_lines lines without bullet marks, cut to most_words words in all."""
    rows = [" ".join(_BULLET.sub("", row).split()) for row in data.splitlines()] if isinstance(data, str) else []
    out, left = [], most_words
    for row in [r for r in rows if r][:most_lines]:
        if left <= 0:
            break
        words = row.split()
        out.append(" ".join(words[:left]) + ("…" if len(words) > left else ""))
        left -= len(words)
    return "\n".join(out)
