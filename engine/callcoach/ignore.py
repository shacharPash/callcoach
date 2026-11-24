"""Names and terms I already know, so the panel does not show them: one per line after "- ", in the data folder's
לא-להקפיץ.md. The app adds a line from an open row in the panel; a line deleted brings the name back from the next
call."""
from __future__ import annotations

from pathlib import Path

FILE = "לא-להקפיץ.md"


def ignore_path(data: Path) -> Path:
    return data / FILE


def load_ignored(data: Path) -> tuple[str, ...]:
    """The names in the file, in its order; none when it is missing or cannot be read."""
    try:
        text = ignore_path(data).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ()
    return tuple(name for name in (line[2:].strip() for line in text.splitlines() if line.startswith("- ")) if name)
