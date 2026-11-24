"""The copy of a finished call for another folder, such as one Google Drive for desktop syncs: its summary.md and a
readable transcript.md, in a folder of the call's own name. Never the audio. The folder comes from CALLCOACH_EXPORT,
which the app sets only when a folder is chosen in its settings."""
from __future__ import annotations

import os
from pathlib import Path

from callcoach.data import ME
from callcoach.store import clock_label, entries, started_at

EXPORT_ENV = "CALLCOACH_EXPORT"
SPEAKERS = {ME: "אני", "them": "הם"}


def export_root(environ=os.environ) -> Path | None:
    value = (environ.get(EXPORT_ENV) or "").strip()
    return Path(value).expanduser() if value else None


def transcript_text(folder: Path) -> str:
    """The call's lines in time order, one per line: the time from the start, who spoke and what was said."""
    lines = sorted((e for e in entries(folder) if e.get("kind") == "line"), key=lambda e: e["t_start"])
    started = started_at(folder)
    rows = [f"[{clock_label(e['t_start'] - started)}] {SPEAKERS.get(e['channel'], e['channel'])}: {e['text']}"
            for e in lines]
    return "# תמלול\n\n" + ("\n".join(rows) if rows else "אין שורות") + "\n"


def _write(path: Path, text: str) -> None:
    """Replaces the file whole, so a sync client never uploads half of it."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def export_call(folder: Path, root: Path | None) -> Path | None:
    """Copies the call into <root>/<call folder name>/ and returns that folder; None when no folder is set. The root
    itself must already exist: a missing one (a Drive that is not mounted, say) raises OSError, rather than creating
    a local folder in its place that is never synced."""
    if root is None:
        return None
    if not root.is_dir():
        raise FileNotFoundError(f"the export folder {root} is not there")
    target = root / folder.name
    target.mkdir(exist_ok=True)
    summary = folder / "summary.md"
    if summary.is_file():
        _write(target / "summary.md", summary.read_text(encoding="utf-8"))
    _write(target / "transcript.md", transcript_text(folder))
    return target


def export_and_mark(folder: Path, root: Path | None) -> str | None:
    """export_call, with how it went kept in meta.json as `export_error` (None after a copy that worked), so the call
    page can say a copy failed however the summary was written. Returns the error, or None."""
    from callcoach.store import update_meta

    if root is None:
        return None
    try:
        export_call(folder, root)
        error = None
    except OSError as e:
        error = f"{type(e).__name__}: {e}"[:300]
    try:
        update_meta(folder, export_error=error)
    except OSError:
        pass
    return error
