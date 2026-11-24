"""lines.jsonl as the source of truth, transcript.md rendered from it. Beside the lines it holds the marked
moments, a note at every change of whether CallCoach hears me and at every pause of the recording."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from callcoach.data import ME, Line

LABELS = {"he": ("אני", "הצד השני"), "en": ("Me", "Them")}
HEARING_NOTES = {False: "מכאן CallCoach לא שומע אותי", True: "מכאן CallCoach שומע אותי שוב"}
PAUSE_NOTES = {True: "מכאן ההקלטה מושהית", False: "מכאן ההקלטה ממשיכה"}


def clock_label(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class TranscriptStore:
    def __init__(self, folder: Path, started: float):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.path = folder / "lines.jsonl"
        self._lock = threading.Lock()
        meta = folder / "meta.json"
        if not meta.exists():
            tmp = folder / "meta.json.tmp"
            tmp.write_text(json.dumps({"started": started}), encoding="utf-8")
            os.replace(tmp, meta)

    def add(self, line: Line) -> None:
        self._append({"kind": "line", **line.to_dict()})

    def bookmark(self, t: float) -> None:
        self._append({"kind": "bookmark", "t": t})

    def hearing(self, on: bool, t: float) -> None:
        """From t, CallCoach hears me (on) or does not."""
        self._append({"kind": "hearing", "on": on, "t": t})

    def paused(self, on: bool, t: float) -> None:
        """From t, the recording is paused (on) or goes on."""
        self._append({"kind": "paused", "on": on, "t": t})

    def render(self) -> Path:
        return render(self.folder)

    def _append(self, entry: dict) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def render(folder: Path) -> Path:
    entries = _entries(folder / "lines.jsonl")
    started = _started(folder, entries)
    rows = [(e["t_start"], _said(e)) for e in entries if e["kind"] == "line"]
    rows += [(e["t"], HEARING_NOTES[bool(e["on"])]) for e in entries if e["kind"] == "hearing"]
    rows += [(e["t"], PAUSE_NOTES[bool(e["on"])]) for e in entries if e["kind"] == "paused"]
    marks = sorted(e["t"] for e in entries if e["kind"] == "bookmark")
    out = [f"# {time.strftime('%Y-%m-%d %H:%M', time.localtime(started))}", ""]
    out += [f"[{clock_label(t - started)}] {text}" for t, text in sorted(rows, key=lambda row: row[0])]
    if marks:
        out += ["", "## Marked moments", ""] + [f"[{clock_label(t - started)}]" for t in marks]
    path = folder / "transcript.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def _said(e: dict) -> str:
    me, them = LABELS.get(e["lang"], LABELS["en"])
    return f"{me if e['channel'] == ME else them}: {e['text']}"


def entries(folder: Path) -> list[dict]:
    return _entries(folder / "lines.jsonl")


def unheard(rows: list[dict]) -> list[tuple[float, float | None]]:
    """The times CallCoach did not hear me, from the hearing notes: (from, to) pairs, `to` None when it still did
    not at the end of the call."""
    return _spans(rows, "hearing", False)


def paused_spans(rows: list[dict]) -> list[tuple[float, float | None]]:
    """The times the recording was paused, from the pause notes, as unheard gives them."""
    return _spans(rows, "paused", True)


def _spans(rows: list[dict], kind: str, start: bool) -> list[tuple[float, float | None]]:
    out: list[tuple[float, float | None]] = []
    since = None
    for e in sorted((e for e in rows if e.get("kind") == kind), key=lambda e: e["t"]):
        if bool(e["on"]) == start and since is None:
            since = e["t"]
        elif bool(e["on"]) != start and since is not None:
            out.append((since, e["t"]))
            since = None
    return out + ([(since, None)] if since is not None else [])


def started_at(folder: Path) -> float:
    return _started(folder, entries(folder))


def _started(folder: Path, entries: list[dict]) -> float:
    try:
        return float(json.loads((folder / "meta.json").read_text(encoding="utf-8"))["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return min((e["t_start"] if e["kind"] == "line" else e["t"] for e in entries), default=time.time())


def _entries(source: Path) -> list[dict]:
    if not source.exists():
        return []
    entries = []
    for raw in source.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def update_meta(folder: Path, **fields) -> None:
    """Adds fields to meta.json, keeping every other key, and replaces the file whole."""
    path = folder / "meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    meta = {**(meta if isinstance(meta, dict) else {}), **fields}
    tmp = folder / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def slug(name: str, default: str) -> str:
    """A name as a file name part: every run of other characters than letters, digits, _ and - becomes one -."""
    return re.sub(r"[^\w-]+", "-", name).strip("-")[:40] or default


def call_folder(data: Path, name: str) -> Path:
    base = data / "calls" / f"{time.strftime('%Y-%m-%d_%H%M')}_{slug(name, 'call')}"
    folder, n = base, 2
    while folder.exists():
        folder = base.with_name(f"{base.name}-{n}")
        n += 1
    folder.mkdir(parents=True)
    return folder
