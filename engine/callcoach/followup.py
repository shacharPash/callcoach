"""The follow-up email after a call: its input for the strong model, and followup.md in the call's folder.

followup.md is a first line "Subject: ...", a blank line, then the body, in the call's language. Nothing is ever
sent: the call page shows the draft and I send it myself."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from callcoach.context import me_text, read_briefs
from callcoach.data import THEM
from callcoach.store import entries, started_at
from callcoach.summary import PAUSED_HEADING, paused_rows, transcript_rows, unheard_rows

FILE = "followup.md"
USAGE_HEADING = "\n## שימוש"
_SUBJECT = re.compile(r"^[\s*#]*(?:subject|נושא)[\s*]*:[\s*]*(.*?)[\s*]*$", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^```\w*[ \t]*$", re.MULTILINE)


class FollowupError(ValueError):
    """A follow-up that cannot be written; the message is Hebrew, for the app to show."""


def call_dir(data: Path, name: str) -> Path:
    """The call's folder: a path, or a folder name in the data folder's calls/."""
    path = Path(name).expanduser()
    folder = path if path.is_absolute() else data / "calls" / name
    if not folder.is_dir():
        raise FollowupError("לא מצאתי את תיקיית השיחה")
    return folder


def _meta(folder: Path) -> dict:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _summary(folder: Path) -> str:
    """summary.md without its usage section, or "" when there is none."""
    try:
        text = (folder / "summary.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text.partition(USAGE_HEADING)[0].strip()


def call_language(lines: list[dict]) -> str:
    """The language most of the other side's words are in (all words when they said none); he when there are
    none."""
    theirs = [e for e in lines if e.get("channel") == THEM] or lines
    words: dict[str, int] = {}
    for e in theirs:
        words[e.get("lang", "he")] = words.get(e.get("lang", "he"), 0) + len(e.get("text", "").split())
    return max(words, key=words.get) if words else "he"


def followup_message(data: Path, folder: Path) -> str:
    """The input for the email: the call's title, time and language, me, the brief it used, its summary, when
    CallCoach did not hear me, and the transcript. A call with no lines has nothing to write from."""
    rows = transcript_rows(folder)
    if not rows:
        raise FollowupError("אין תמלול בשיחה הזאת, אין ממה לכתוב מייל")
    meta, started = _meta(folder), started_at(folder)
    lines = [e for e in entries(folder) if e.get("kind") == "line"]
    parts = ["# Call", f"title: {meta.get('title') or '(none)'}",
             f"when: {time.strftime('%Y-%m-%d %H:%M', time.localtime(started))}",
             f"call language: {call_language(lines)}", "", "# Me", me_text(data) or "(empty)"]
    brief = meta.get("brief")
    for name, text in read_briefs(data, [brief]) if isinstance(brief, str) and brief else []:
        parts += ["", f"# Brief: {name}", text]
    parts += ["", "# Summary", _summary(folder) or "(none)"]
    off = unheard_rows(folder, started)
    if off:
        parts += ["", "# When CallCoach did not hear me (I may have spoken then)", *off]
    paused = paused_rows(folder, started)
    if paused:
        parts += ["", PAUSED_HEADING, *paused]
    return "\n".join([*parts, "", "# Transcript", *rows]) + "\n"


def followup_text(reply) -> str:
    """The email in the model's reply as "Subject: ...", a blank line and the body; a reply without a subject line
    near its top or without a body is not an email."""
    lines = _CODE_FENCE.sub("", reply if isinstance(reply, str) else "").strip().splitlines()
    at = next((i for i, line in enumerate(lines[:3]) if _SUBJECT.match(line)), None)
    if at is None:
        raise FollowupError("המודל לא כתב שורת נושא, אפשר לנסות שוב")
    subject = " ".join(_SUBJECT.match(lines[at]).group(1).split())
    body = "\n".join(lines[at + 1:]).strip()
    if not subject or not body:
        raise FollowupError("המודל החזיר טיוטה ריקה, אפשר לנסות שוב")
    return f"Subject: {subject}\n\n{body}\n"


def write_followup(folder: Path, text: str) -> Path:
    """followup.md, replaced whole, so the call page never reads half of it."""
    path = folder / FILE
    tmp = folder / f".{FILE}.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path
