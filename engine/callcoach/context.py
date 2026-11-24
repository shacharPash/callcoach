"""What the agent knows: me, library cards, the call type and this call. Plain files in the data folder."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from callcoach.brief import MAX_QUESTIONS, brief_terms, parse_brief, question_parts
from callcoach.cards import Question, question
from callcoach.config import DEFAULTS_DIR
from callcoach.knowledge import knowledge_paths, local_only
from callcoach.prompts import STEP_RULES
from callcoach.protocol import LANGS

DEFAULT_CARDS = ("question", "tip", "steer")
ADDRESS_KEY = "address me as"
MAX_KNOWLEDGE_CHARS = 60_000
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class Goal:
    id: str
    text: str
    done: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "done": self.done}


@dataclass(frozen=True)
class Prep:
    call_type: str = "general"
    lang: str = "he"
    level: str = "normal"
    text: str = ""
    brief: str = ""
    length_min: float | None = None
    goals: tuple[Goal, ...] = ()
    glossary: tuple[str, ...] = ()
    name: str = "call"
    title: str = ""
    questions: tuple[Question, ...] = ()
    replay: str | None = None
    # Only the recording and the live transcript: no agent, no summary, and the panel starts hidden.
    record_only: bool = False
    # The sections (ask, terms, now) the call starts with; None starts with all open for a call with the coach and all
    # closed for a recording. A sections object without now keeps the now line off.
    sections: tuple[bool, bool, bool] | None = None
    # Terms only from the knowledge files and the brief ("רק מהרשימה"), never asked of the live model.
    list_only: bool = False

    @classmethod
    def from_command(cls, cmd: dict) -> "Prep":
        return cls(call_type=cmd["call_type"], lang=cmd["lang"], level=cmd["level"], text=cmd["text"],
                   brief=cmd["brief"], length_min=cmd["length_min"],
                   goals=tuple(Goal(g["id"], g["text"], bool(g.get("done", False))) for g in cmd.get("goals", [])),
                   glossary=tuple(cmd.get("glossary", [])), name=cmd.get("name") or cmd["call_type"],
                   title=cmd["title"].strip(), questions=opening_questions(cmd["questions"]),
                   replay=cmd.get("replay") or None, record_only=bool(cmd.get("record_only", False)),
                   sections=(cmd["sections"]["ask"], cmd["sections"]["terms"], cmd["sections"].get("now", False))
                   if "sections" in cmd else None,
                   list_only=bool(cmd.get("sections", {}).get("list_only", False)))

    @property
    def first_lang(self) -> str:
        """The language the call starts in: auto starts in Hebrew, and detection decides from there."""
        return self.lang if self.lang in LANGS else "he"


def opening_questions(items) -> tuple[Question, ...]:
    """The opening questions of a start: each "text | he" or the text alone, the first MAX_QUESTIONS."""
    found = (question(*question_parts(item)) for item in items)
    return tuple(q for q in found if q.text)[:MAX_QUESTIONS]


@dataclass(frozen=True)
class CallType:
    """A call type file: library cards, the card kinds it allows, a goal template and guidance. An old file's
    `cadence` key is ignored: every call listens the same way."""
    name: str
    library: tuple[str, ...] = ()
    cards: tuple[str, ...] = DEFAULT_CARDS
    goals: tuple[str, ...] = ()
    guidance: str = ""


def _split(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def parse_call_type(name: str, text: str) -> CallType:
    head, _, body = text.partition("\n---")
    fields: dict[str, str] = {}
    goals: list[str] = []
    in_goals = False
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_goals and line.startswith("- "):
            goals.append(line[2:].strip())
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        in_goals = key == "goals"
        if not in_goals:
            fields[key] = value.strip()
    return CallType(name=name, library=_split(fields.get("library", "")),
                    cards=_split(fields.get("cards", "")) or DEFAULT_CARDS, goals=tuple(goals), guidance=body.strip())


def ensure_data(data: Path) -> None:
    """Copies the default me.md, library cards and call types into the data folder, never over a file."""
    for src in sorted(DEFAULTS_DIR.rglob("*.md")):
        dst = data / src.relative_to(DEFAULTS_DIR)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    (data / "briefs").mkdir(parents=True, exist_ok=True)


def _read(path: Path) -> str:
    """The file's text, or "" when it cannot be read or is not UTF-8."""
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def address_form(me: str) -> str:
    """The form after "Address me as:" in me.md, or "" when it is empty or still the template's hint."""
    for line in _COMMENT.sub("", me).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == ADDRESS_KEY:
            value = value.strip()
            return "" if value.startswith("(") else value
    return ""


def address_of(data: Path) -> str:
    return address_form(_read(data / "me.md"))


def me_text(data: Path) -> str:
    """me.md as the agent reads it: without template comments, and "not given" for an empty address."""
    lines = []
    for line in _COMMENT.sub("", _read(data / "me.md")).splitlines():
        key, sep, _ = line.partition(":")
        if sep and key.strip().lower() == ADDRESS_KEY:
            line = f"{key.strip()}: {address_form(line) or 'not given'}"
        lines.append(line)
    return "\n".join(lines).strip()


def _plain(name: str) -> bool:
    return bool(name) and Path(name).name == name and not name.startswith(".")


def load_call_type(data: Path, name: str) -> CallType:
    if not _plain(name):
        return CallType(name="general")
    for folder in (data / "call-types", DEFAULTS_DIR / "call-types"):
        path = folder / f"{name}.md"
        if path.is_file():
            return parse_call_type(name, path.read_text(encoding="utf-8"))
    return CallType(name=name)


def read_briefs(data: Path, names) -> list[tuple[str, str]]:
    """The body of each brief, without its header."""
    folder = data / "briefs"
    return [(n, parse_brief(_read(folder / n))[1]) for n in names if _plain(n) and (folder / n).is_file()]


def read_brief_terms(data: Path, name: str) -> tuple[tuple[str, str], ...]:
    """The terms in the header of a brief, as (term, Hebrew note) pairs; none for a name that is not a brief."""
    path = data / "briefs" / name
    return brief_terms(parse_brief(_read(path))[0]) if _plain(name) and path.is_file() else ()


def call_type_names(data: Path) -> list[str]:
    """Every call type there is: the data folder's own and the defaults."""
    folders = [f for f in (data / "call-types", DEFAULTS_DIR / "call-types") if f.is_dir()]
    return sorted({p.stem for f in folders for p in f.glob("*.md") if _plain(p.name)})


def first_sentence(text: str) -> str:
    """A call type's guidance in one line: its first sentence."""
    text = " ".join(text.split())
    head, sep, _ = text.partition(". ")
    return head + "." if sep else text


def library_cards(data: Path, call_type: CallType) -> list[tuple[str, str]]:
    out = []
    for name in call_type.library:
        text = _read(data / "library" / f"{name}.md") if _plain(name) else ""
        if text:
            out.append((name, text))
    return out


def build_prefix(data: Path, prep: Prep, call_type: CallType) -> str:
    parts = [STEP_RULES.strip(), "", "# Me", me_text(data) or "(empty)", ""]
    for name, text in library_cards(data, call_type):
        parts += [f"# Library: {name}", text, ""]
    parts += [f"# Call type: {call_type.name}",
              f"allowed_cards: {', '.join(k for k in call_type.cards if k != 'term') or '(none)'}",
              call_type.guidance, "",
              "# This call", prep.text.strip() or "(no notes)"]
    if prep.length_min:
        parts.append(f"planned length: {prep.length_min:g} min")
    for name, text in read_briefs(data, [prep.brief]):
        parts += ["", f"# Brief: {name}", text]
    if prep.glossary:
        parts += ["", "# Glossary", ", ".join(prep.glossary)]
    parts += ["", "# Goals"] + ([f"- [{g.id}] {g.text}" for g in prep.goals] or ["(none)"])
    return "\n".join(parts) + "\n"


def prep_message(data: Path, prep: Prep, call_type: CallType) -> str:
    parts = ["# Me", me_text(data) or "(empty)", "", f"# Call type: {call_type.name}", call_type.guidance,
             "goal template:"] + [f"- {g}" for g in call_type.goals] + ["", f"call language: {prep.lang}"]
    if prep.length_min:
        parts.append(f"planned length: {prep.length_min:g} min")
    parts += ["", "# My notes", prep.text.strip() or "(none)"]
    for name, text in read_briefs(data, [prep.brief]):
        parts += ["", f"# Brief: {name}", text]
    return "\n".join(parts) + "\n"


def knowledge_files(data: Path, limit: int = MAX_KNOWLEDGE_CHARS) -> list[tuple[str, str]]:
    """The knowledge files as they are written, cut to `limit` characters in all. A file marked local only is left
    out whole: its names still show in the panel, but what it says stays on this Mac."""
    out = []
    for path in knowledge_paths(data):
        text = _read(path)
        if local_only(text):
            continue
        text = text[:limit]
        if text:
            out.append((path.stem, text))
            limit -= len(text)
        if limit <= 0:
            break
    return out


def request_message(request: dict) -> list[str]:
    """The `callcoach brief` request as the models read it; the research step is given nothing else."""
    parts = [f"title: {request['title'] or '(none)'}", f"when: {request['when'] or '(none)'}", "",
             "## What the call is about", request["text"].strip() or "(none)"]
    for f in request["files"]:
        parts += ["", f"## File: {f['name']}", f["text"].strip() or "(empty)"]
    return parts + ["", "## Links", *([f"- {link}" for link in request["links"]] or ["(none)"])]


def research_message(request: dict) -> str:
    return "\n".join(["# Request", *request_message(request)]) + "\n"


def brief_message(data: Path, request: dict, research: str, now: datetime) -> str:
    """The input for writing the brief: the request, the research notes, my profile, the call types and the
    knowledge files."""
    parts = ["# Request", f"now: {now:%Y-%m-%d %H:%M} ({now:%A})", *request_message(request), "",
             "# Research notes", research.strip() or "(none)"]
    parts += ["", "# Me", me_text(data) or "(empty)", "", "# Call types"]
    parts += [f"- {name}: {first_sentence(load_call_type(data, name).guidance)}" for name in call_type_names(data)]
    files = [f'<knowledge file="{name}">\n{text}\n</knowledge>' for name, text in knowledge_files(data)]
    parts += ["", "# Knowledge files", *(files or ["(none)"])]
    return "\n".join(parts) + "\n"

