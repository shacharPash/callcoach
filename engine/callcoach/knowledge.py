"""Knowledge files: the names, companies, funds and terms I am expected to know, found as the transcript comes.

Each `*.md` file in the data folder's knowledge/ holds "## <name>" entries with the fields סוג, כתיבים, תקציר,
פרטים and שלנו (the header of the file explains them). A file whose header (before its first entry) has the line
"למודל: לא" is local only: its names are still found in the transcript, but the file is never sent to a model.
A spelling that ends with * is also an everyday word: it is not matched in the transcript on its own, but a term
the agent names still finds its entry through it. A person's one word name is matched only as a whole word, so
"רון" is not found in "שרון".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KINDS = {"חברה": "company", "קרן": "fund", "אדם": "person", "ארגון": "org", "אירוע": "event", "מונח": "term",
         "מוצר": "product"}
FIELDS = ("סוג", "כתיבים", "תקציר", "פרטים", "שלנו", "מקורות", "דוגמאות")
NONE_YET = "אין עדיין"
LOCAL_ONLY = "למודל: לא"
EVERYDAY = "*"
PREFIXES = "והבלמשכ"
MAX_PREFIX = 2

_DASHES = re.compile(r"[-־‐-―./]")
_MARKS = re.compile(r"[֑-ׇ'\"`׳״‘’“”]")
_WORD = re.compile(r"[^\W_]+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_PART = re.compile(r"\s*,?\s*(?:ב-)?(\d{4})-(\d{2})-\d{2}")
_SOURCE = re.compile(r",\s*מקור:.*$")
_SOURCE_LINK = re.compile(r",\s*מקור:.*\((https?://[^\s)]+)\)\s*$", re.IGNORECASE)
MONTHS = ("ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר")


def words(text: str) -> tuple[str, ...]:
    """The words a name is matched by: case, niqqud, geresh and quotes folded, dashes and dots as spaces."""
    return tuple(_WORD.findall(_MARKS.sub("", _DASHES.sub(" ", text.casefold()))))


def ours_line(item: str) -> str:
    """A שלנו bullet as the panel shows it: the month first, so a cut line keeps it, and no source."""
    text = _SOURCE.sub("", item).strip()
    found = _DATE_PART.search(text)
    if not found or not 1 <= int(found.group(2)) <= 12:
        return text
    rest = (text[:found.start()] + text[found.end():]).strip(" ,")
    month = f"{MONTHS[int(found.group(2)) - 1]} {found.group(1)}"
    return f"{month}: {rest}" if rest else month


def ours_link(item: str) -> str:
    """The web link of a שלנו bullet's source, for the panel to open; "" when its source has none."""
    found = _SOURCE_LINK.search(item)
    return found.group(1) if found else ""


def _newest_first(items: list[str]) -> tuple[str, ...]:
    def date(item: str) -> str:
        found = _DATE.search(_SOURCE.sub("", item))
        return found.group(0) if found else ""

    return tuple(sorted(items, key=date, reverse=True))


def _bare(word: str) -> list[str]:
    """The word, and the word without one or two Hebrew prefix letters."""
    out = [word]
    for n in range(1, MAX_PREFIX + 1):
        if len(word) - n < 2 or word[n - 1] not in PREFIXES:
            break
        out.append(word[n:])
    return out


def _latin(text: str) -> bool:
    """Written in Latin letters with no Hebrew, as a term said in English is."""
    return bool(re.search(r"[A-Za-z]", text)) and not re.search(r"[֐-׿]", text)


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    summary: str
    spellings: tuple[str, ...] = ()
    ours: tuple[str, ...] = ()
    file: str = ""
    # The פרטים bullets, in the order written: the detail a click on the name shows with no model request.
    details: tuple[str, ...] = ()
    # From a file marked local only (see local_only), so its details are never put into a model request.
    local: bool = False

    @property
    def lang(self) -> str:
        return "he" if re.search(r"[֐-׿]", self.summary) else "en"

    def shown(self, said: str = "") -> str:
        """The name the panel shows. An entry named in Hebrew that was said in English shows as it was said, so a
        term heard in English reads in English; otherwise the entry's own name, which also corrects a misheard
        spelling of a Latin name."""
        return said if said and _latin(said) and not _latin(self.name) else self.name

    def keys(self) -> tuple[set, set]:
        """The folded spellings matched on their own, and those marked as everyday words."""
        everyday = {words(s.rstrip(EVERYDAY)) for s in self.spellings if s.endswith(EVERYDAY)}
        named = {words(s) for s in (self.name, *self.spellings) if not s.endswith(EVERYDAY)}
        return {k for k in named - everyday if k}, {k for k in everyday if k}


def parse(text: str, file: str = "") -> tuple[list[Entry], int]:
    """The entries of one file (named `file`, without .md), and how many were skipped for having no תקציר."""
    local = local_only(text)
    entries: list[Entry] = []
    skipped = 0
    current: dict | None = None
    field = None

    def close() -> None:
        nonlocal skipped
        if current is None:
            return
        if not current["summary"]:
            skipped += 1
            return
        entries.append(Entry(current["name"], KINDS.get(current["kind"], "term"), current["summary"],
                             tuple(s.strip() for s in current["spellings"].split(",") if s.strip()),
                             _newest_first(current["ours"]), file, tuple(current["details"]), local))

    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("## "):
            close()
            current, field = {"name": raw[3:].strip(), "kind": "", "spellings": "", "summary": "", "ours": [],
                                "details": []}, None
        elif raw.startswith("# "):
            close()
            current, field = None, None
        elif current is None:
            continue
        elif line.startswith("- "):
            if field == "שלנו":
                current["ours"].append(line[2:].strip())
            elif field == "פרטים" and line[2:].strip():
                current["details"].append(line[2:].strip())
        else:
            key, sep, value = line.partition(":")
            if sep and key.strip() in FIELDS:
                field, value = key.strip(), value.strip()
                if field == "סוג":
                    current["kind"] = value
                elif field == "כתיבים":
                    current["spellings"] = value
                elif field == "תקציר":
                    current["summary"] = value
                elif field == "שלנו" and value and value != NONE_YET:
                    current["ours"].append(value)
                elif field == "פרטים" and value and value != NONE_YET:
                    current["details"].append(value)
    close()
    return entries, skipped


def local_only(text: str) -> bool:
    """Whether a knowledge file is marked as never sent to a model: a line exactly "למודל: לא" before its first
    entry. Only the header counts, so an entry that quotes the line does not hide the whole file."""
    for raw in text.splitlines():
        if raw.startswith("## "):
            return False
        if raw.strip() == LOCAL_ONLY:
            return True
    return False


def _claim(index: dict, key, entry: Entry) -> None:
    """A key that two entries claim belongs to neither."""
    index[key] = entry if index.get(key, entry) is entry else None


class Knowledge:
    """The entries of all knowledge files, indexed by their folded spellings. An entry's name is its own; a
    spelling of two entries belongs to neither."""

    def __init__(self, entries=(), *, files: tuple[str, ...] = (), skipped: int = 0, failed: tuple[str, ...] = ()):
        names: dict[tuple, Entry] = {}
        for entry in entries:
            name = words(entry.name)
            if name and name not in names:
                names[name] = entry
        self.entries = tuple(names.values())
        everyone: dict[tuple, Entry | None] = {}
        joined: dict[str, Entry | None] = {}
        own: list[tuple[set, Entry]] = []
        for entry in self.entries:
            mine = lambda keys: {k for k in keys if names.get(k, entry) is entry}
            named, everyday = map(mine, entry.keys())
            own.append((named, entry))
            for key in named | everyday:
                _claim(everyone, key, entry)
                _claim(joined, "".join(key), entry)
        self.files = files
        self.skipped = skipped
        self.failed = failed
        self.ambiguous = tuple(sorted(" ".join(k) for k, e in everyone.items() if e is None))
        self._everyone = {k: e for k, e in everyone.items() if e is not None}
        self._joined = {k: e for k, e in joined.items() if e is not None}
        self._named = {k: e for named, e in own for k in named if self._everyone.get(k) is e}
        self._longest = max((len(k) for k in self._named), default=0)

    def find(self, text: str) -> list[Entry]:
        """The entries named in a line, each once, in the order they were said; the longest name wins."""
        return [entry for entry, _ in self.said_in(text)]

    def said_in(self, text: str) -> list[tuple[Entry, str]]:
        """The entries named in a line, each once, in the order they were said, each with the spelling it was said
        by (the first time); the longest name wins."""
        tokens = words(text)
        found: list[tuple[Entry, str]] = []
        i = 0
        while i < len(tokens):
            match = self._match(tokens, i)
            if match is None:
                i += 1
                continue
            entry, n, key = match
            if all(entry is not seen for seen, _ in found):
                found.append((entry, self._spelling(entry, key)))
            i += n
        return found

    @staticmethod
    def _spelling(entry: Entry, key: tuple[str, ...]) -> str:
        for spelling in (entry.name, *entry.spellings):
            if words(spelling.rstrip(EVERYDAY)) == key:
                return spelling.rstrip(EVERYDAY)
        return entry.name

    def _match(self, tokens: tuple[str, ...], i: int) -> tuple[Entry, int, tuple[str, ...]] | None:
        for n in range(min(self._longest, len(tokens) - i), 0, -1):
            rest = tokens[i + 1:i + n]
            for stripped, first in enumerate(_bare(tokens[i])):
                entry = self._named.get((first, *rest))
                if entry is not None and not (stripped and n == 1 and entry.kind == "person"):
                    return entry, n, (first, *rest)
        return None

    def lookup(self, term: str) -> Entry | None:
        """The entry a term the agent named stands for, by its whole text, everyday spellings included."""
        key = words(term)
        return (self._everyone.get(key) or self._joined.get("".join(key))) if key else None

    def describe(self) -> str:
        """The files and entries for the preparation window, and the files that could not be read."""
        parts = [f"{', '.join(self.files)}: {len(self.entries)} רשומות"] if self.entries else []
        if self.failed:
            parts.append(f"לא נקרא: {', '.join(self.failed)}")
        return ", ".join(parts)


def knowledge_paths(data: Path) -> list[Path]:
    """The knowledge files in the data folder, in name order; a backup copy is not one."""
    folder = data / "knowledge"
    return sorted(p for p in folder.glob("*.md") if ".backup" not in p.name) if folder.is_dir() else []


def load_knowledge(data: Path) -> Knowledge:
    """Every knowledge file in the data folder, read in name order."""
    entries: list[Entry] = []
    files: list[str] = []
    failed: list[str] = []
    skipped = 0
    for path in knowledge_paths(data):
        try:
            found, missing = parse(path.read_text(encoding="utf-8-sig"), path.stem)
        except (OSError, UnicodeDecodeError):
            failed.append(path.stem)
            continue
        if found:
            files.append(path.stem)
            entries += found
        skipped += missing
    return Knowledge(entries, files=tuple(files), skipped=skipped, failed=tuple(failed))
