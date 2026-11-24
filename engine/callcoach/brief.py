"""The brief file: a header with the call's settings and a Hebrew body of context, in the data folder's briefs/.

The header sits between a first line --- (blank lines may come before it) and the next ---, one `key: value` per
line, and a value may be in quotes. Goals, questions, glossary and terms are lists: `  - item` lines, or `[a, b]`; a
glossary may also be comma separated. A question may carry its Hebrew translation after " | ", and a term is
"Term: Hebrew note". Every key is optional and unknown keys are ignored. A file with no header is plain context. It is
read as the app reads it (app/Brief.swift)."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from callcoach.protocol import LANGS
from callcoach.store import slug

FENCE = "---"
WHEN_FORMAT = "%Y-%m-%d %H:%M"
TEXT_KEYS = ("title", "when", "type", "lang", "length")
LIST_KEYS = ("goals", "questions", "terms")
MAX_MINUTES = 600
MAX_GOALS = 5
MAX_QUESTIONS = 3
MAX_TERMS = 20
MAX_TERM_NOTE_WORDS = 12
TRANSLATION = " | "
_TRANSLATION = re.compile(r"\s\|(?:\s|$)")
_CODE_FENCE = re.compile(r"^```\w*[ \t]*$", re.MULTILINE)
_NUMBER = re.compile(r"[0-9.]*")


class BriefError(ValueError):
    """A brief request that cannot be done; the message is Hebrew, for the app to show."""


def _unquoted(value: str) -> str:
    value = value.strip()
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1].strip()
    return value


def _list(value: str, items: list[str], commas: bool) -> list[str]:
    """A list key: its `- item` lines, else a value `[a, b]`, else the value as one item (split at commas for the
    glossary)."""
    if any(items):
        return [i for i in items if i]
    bracketed = value.startswith("[") and value.endswith("]")
    inner = value[1:-1] if bracketed else value
    return [v for v in map(_unquoted, inner.split(",") if bracketed or commas else [inner]) if v]


def _header_span(lines: list[str]) -> tuple[int, int] | None:
    """Where the header starts and ends: a --- as the first line that is not blank, and the next ---."""
    start = next((i for i, line in enumerate(lines) if line.strip()), None)
    if start is None or lines[start].strip() != FENCE:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == FENCE), None)
    return None if end is None else (start, end)


def question_parts(item: str) -> tuple[str, str]:
    """A question and its Hebrew translation, split at the first " | " (a bar with a space before it and a space or
    the end after it); ("", "") for an empty question."""
    text, he = [*_TRANSLATION.split(item, maxsplit=1), ""][:2]
    text, he = " ".join(text.split()), " ".join(he.split())
    return (text, he) if text else ("", "")


def question_item(item: str) -> str:
    """A question as the header writes it: "text | he", or the text alone."""
    text, he = question_parts(item)
    return f"{text}{TRANSLATION}{he}" if text and he else text


def term_parts(item: str) -> tuple[str, str]:
    """A brief term and its Hebrew note, split at the first ":" (the note cut to MAX_TERM_NOTE_WORDS words); ("", "")
    when either is missing."""
    term, _, note = item.partition(":")
    term, note = " ".join(term.split()), " ".join(note.split()[:MAX_TERM_NOTE_WORDS])
    return (term, note) if term and note else ("", "")


def parse_brief(text: str) -> tuple[dict, str]:
    """The header as a dict (lists for goals, questions, glossary and terms) and the body."""
    lines = text.lstrip("\ufeff").splitlines()
    found = _header_span(lines)
    if found is None:
        return {}, "\n".join(lines).strip()
    start, end = found
    values: dict[str, str] = {}
    items: dict[str, list[str]] = {}
    key = None
    for raw in lines[start + 1:end]:
        line = raw.strip()
        if line == "-" or line.startswith("- "):
            if key:
                items.setdefault(key, []).append(_unquoted(line[1:]))
            continue
        name, sep, value = line.partition(":")
        if sep:
            key = name.strip().lower()
            values[key] = _unquoted(value)
    header: dict = {k: values[k] for k in TEXT_KEYS if k in values}
    for k in (*LIST_KEYS, "glossary"):
        if k in values or k in items:
            header[k] = _list(values.get(k, ""), items.get(k, []), commas=k == "glossary")
    return header, "\n".join(lines[end + 1:]).strip()


def brief_title(header: dict, body: str, name: str) -> str:
    """The header's title, else the body's first "# " heading, else the file name without .md."""
    heading = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
    return header.get("title") or heading or Path(name).stem


def parse_when(value) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip(), WHEN_FORMAT)
    except ValueError:
        return None


def _minutes(value) -> float | None:
    """The number a length starts with, in minutes, up to 600."""
    try:
        minutes = float(_NUMBER.match(str(value or "").strip()).group())
    except ValueError:
        return None
    return minutes if 0 < minutes <= MAX_MINUTES else None


def _items(value, limit: int | None = None) -> list[str]:
    items = [" ".join(str(v).split()) for v in value] if isinstance(value, list) else []
    return [v for v in items if v][:limit]


def normalize(header: dict, types) -> dict:
    """The header with every value checked: a type that is not one of `types` is general, a language other than he
    or en is auto (both in any case), a `when` that is not local "YYYY-MM-DD HH:MM" and a length that does not start
    with a number from above 0 to 600 are dropped, goals keep the first 5, questions the first 3 (each "text | he"
    or the text) and terms the first 20 that have a note."""
    out: dict = {}
    title = " ".join(str(header.get("title") or "").split())
    if title:
        out["title"] = title
    when = parse_when(header.get("when"))
    if when:
        out["when"] = when.strftime(WHEN_FORMAT)
    call_type = str(header.get("type") or "").strip().lower()
    out["type"] = call_type if call_type in set(types) else "general"
    lang = str(header.get("lang") or "").strip().lower()
    out["lang"] = lang if lang in LANGS else "auto"
    length = _minutes(header.get("length"))
    if length:
        out["length"] = length
    questions = [q for q in map(question_item, _items(header.get("questions"))) if q][:MAX_QUESTIONS]
    lists = {"goals": _items(header.get("goals"), MAX_GOALS), "questions": questions,
             "glossary": _items(header.get("glossary")),
             "terms": [f"{term}: {note}" for term, note in brief_terms(header)]}
    out.update({key: items for key, items in lists.items() if items})
    return out


def brief_terms(header: dict) -> tuple[tuple[str, str], ...]:
    """The header's terms as (term, note) pairs: the first of each name, up to MAX_TERMS."""
    found: dict[str, tuple[str, str]] = {}
    for item in _items(header.get("terms")):
        term, note = term_parts(item)
        if term:
            found.setdefault(term.casefold(), (term, note))
    return tuple(found.values())[:MAX_TERMS]


def render_brief(header: dict, body: str) -> str:
    lines = [FENCE]
    for key in TEXT_KEYS:
        if key in header:
            value = header[key]
            lines.append(f"{key}: {value:g}" if key == "length" else f"{key}: {value}")
    for key in ("goals", "questions"):
        if header.get(key):
            lines += [f"{key}:", *(f"  - {item}" for item in header[key])]
    if header.get("glossary"):
        lines.append("glossary: " + ", ".join(header["glossary"]))
    if header.get("terms"):
        lines += ["terms:", *(f"  - {item}" for item in header["terms"])]
    return "\n".join([*lines, FENCE, body.strip()]).rstrip() + "\n"


def write_brief(folder: Path, header: dict, body: str, today: date | None = None) -> Path:
    """Writes `<YYYY-MM-DD>-<slug>.md` (the date of `when`, else today; the slug from the title), with -2, -3 when
    the name is taken. The file appears whole and never replaces another one."""
    folder.mkdir(parents=True, exist_ok=True)
    day = header["when"][:10] if header.get("when") else (today or date.today()).isoformat()
    base = f"{day}-{slug(header.get('title', ''), 'brief')}"
    tmp = folder / f".{base}.{os.getpid()}.tmp"
    tmp.write_text(render_brief(header, body), encoding="utf-8")
    try:
        n = 1
        while True:
            path = folder / (f"{base}.md" if n == 1 else f"{base}-{n}.md")
            try:
                os.link(tmp, path)
                return path
            except FileExistsError:
                n += 1
    finally:
        tmp.unlink(missing_ok=True)


def read_request(raw: str) -> dict:
    """The `callcoach brief` request: text, and optionally title, when, files ({name, text}) and links."""
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        raise BriefError("הבקשה אינה JSON תקין") from None
    if not isinstance(request, dict) or not isinstance(request.get("text"), str):
        raise BriefError("בבקשה חסר השדה text")
    files, links = request.get("files") or [], request.get("links") or []
    if not isinstance(files, list) or not all(
            isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("text"), str) for f in files):
        raise BriefError("השדה files צריך להיות רשימה של קבצים עם name ו-text")
    if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
        raise BriefError("השדה links צריך להיות רשימה של כתובות")
    for key in ("title", "when"):
        if not isinstance(request.get(key) or "", str):
            raise BriefError(f"השדה {key} צריך להיות טקסט")
    out = {"title": (request.get("title") or "").strip(), "when": (request.get("when") or "").strip(),
           "text": request["text"], "files": [{"name": f["name"], "text": f["text"]} for f in files],
           "links": [link.strip() for link in links if link.strip()]}
    if not (out["title"] or out["text"].strip() or out["files"] or out["links"]):
        raise BriefError("אין ממה לכתוב תדריך: הבקשה ריקה")
    return out


def reply_text(reply) -> str:
    """The brief in the model's reply: from its first --- line, without code fences."""
    text = _CODE_FENCE.sub("", reply if isinstance(reply, str) else "")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == FENCE), 0)
    return "\n".join(lines[start:]).strip()


def save_reply(folder: Path, reply, request: dict, types, today: date | None = None) -> Path:
    """Checks the brief the model wrote, with the request's title and when in place of its own, and writes it."""
    text = reply_text(reply)
    if not text:
        raise BriefError("המודל החזיר תשובה ריקה")
    header, body = parse_brief(text)
    given = {key: request[key] for key in ("title", "when") if request.get(key)}
    return write_brief(folder, normalize({**header, **given}, types), body, today)
