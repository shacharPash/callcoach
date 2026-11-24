"""The summary after a call: the input for the strong model, a local fallback, summary.md and terms.json.

The summary picks its own form: lecture notes when my lines hold less than a tenth of the transcript's words (or there
are none), else the call summary."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from callcoach.context import Goal, Prep
from callcoach.data import ME
from callcoach.prompts import LECTURE_RULES, SUMMARY_RULES
from callcoach.store import clock_label, entries, paused_spans, started_at, unheard

MARK_BEFORE_S = 20.0
MARK_AFTER_S = 10.0
LECTURE_SHARE = 0.10
TERM_FIELDS = ("term", "note", "kind", "lang", "known")


@dataclass(frozen=True)
class SummaryResult:
    ok: bool
    path: Path
    message: str
    kind: str = "call"


def _call(folder: Path) -> tuple[list[dict], list[float], float]:
    rows = entries(folder)
    lines = sorted((e for e in rows if e.get("kind") == "line"), key=lambda e: e["t_start"])
    marks = sorted(e["t"] for e in rows if e.get("kind") == "bookmark")
    return lines, marks, started_at(folder)


def _row(e: dict, started: float) -> str:
    return f"[{clock_label(e['t_start'] - started)}] {e['channel']} ({e['lang']}): {e['text']}"


def _quotes(lines: list[dict], t: float) -> list[dict]:
    return [e for e in lines if t - MARK_BEFORE_S <= e["t_start"] <= t + MARK_AFTER_S]


def transcript_rows(folder: Path) -> list[str]:
    """The call's lines in time order, as the strong model reads them."""
    lines, _, started = _call(folder)
    return [_row(e, started) for e in lines]


def summary_kind(lines: list[dict]) -> str:
    """lecture when my lines hold less than LECTURE_SHARE of the transcript's words, or there are none; else call."""
    mine = sum(len(e["text"].split()) for e in lines if e["channel"] == ME)
    total = sum(len(e["text"].split()) for e in lines)
    return "lecture" if not mine or mine < LECTURE_SHARE * total else "call"


def unheard_rows(folder: Path, started: float) -> list[str]:
    """The times CallCoach did not hear me, one per line."""
    return [f"- [{clock_label(off - started)}] to " + (f"[{clock_label(on - started)}]" if on else "the end")
            for off, on in unheard(entries(folder))]


def paused_rows(folder: Path, started: float) -> list[str]:
    """The times the recording was paused, one per line."""
    return [f"- [{clock_label(since - started)}] to " + (f"[{clock_label(to - started)}]" if to else "the end")
            for since, to in paused_spans(entries(folder))]


PAUSED_HEADING = "# When the recording was paused (neither side was recorded)"


def build_input(folder: Path, prep: Prep, goals: list[Goal], address: str = "") -> str:
    lines, marks, started = _call(folder)
    parts = ["# Call", f"type: {prep.call_type}", f"name: {prep.name}", f"address me as: {address or 'not given'}",
             "notes:", prep.text.strip() or "(none)", "", "# Goals"]
    parts += [f"- {'answered' if g.done else 'open'}: {g.text}" for g in goals] or ["(none)"]
    parts += ["", "# Glossary", ", ".join(getattr(prep, "glossary", None) or []) or "(none)"]
    off = unheard_rows(folder, started)
    if off:
        parts += ["", "# When CallCoach did not hear me (I may have spoken then)", *off]
    paused = paused_rows(folder, started)
    if paused:
        parts += ["", PAUSED_HEADING, *paused]
    parts += ["", "# Marked moments"]
    for t in marks:
        parts.append(f"- [{clock_label(t - started)}]")
        parts += [f"  {_row(e, started)}" for e in _quotes(lines, t)]
    if not marks:
        parts.append("(none)")
    parts += ["", "# Transcript"] + ([_row(e, started) for e in lines] or ["(no lines)"])
    return "\n".join(parts) + "\n"


def local_summary(folder: Path, goals: list[Goal], reason: str) -> str:
    lines, marks, started = _call(folder)
    voiced = {"me": 0.0, "them": 0.0}
    for e in lines:
        voiced[e["channel"]] += max(0.0, e["speech_end"] - e["t_start"])
    total = sum(voiced.values())
    out = ["## סיכום מקומי", f"הסיכום המלא לא נכתב ({reason}).", "", "## מטרות"]
    out += [f"- מטרה {'שהושגה' if g.done else 'פתוחה'}: {g.text}" for g in goals] or ["- אין מטרות"]
    out += ["", "## רגעים מסומנים"]
    for t in marks:
        quote = " / ".join(e["text"] for e in _quotes(lines, t)) or "אין שורות"
        out.append(f"- רגע מסומן ב-{clock_label(t - started)}: {quote}")
    if not marks:
        out.append("- אין רגעים מסומנים")
    if total:
        out += ["", f"חלק הדיבור שלי: {round(100 * voiced[ME] / total)}%"]
    return "\n".join(out) + "\n"


def usage_section(call: dict) -> str:
    return "\n".join([
        f"בקשות: {call['requests']}",
        f"טוקנים: {call['input']:,} קלט, {call['output']:,} פלט, {call['cache_read']:,} מהמטמון, "
        f"{call['cache_write']:,} כתיבה למטמון",
        f"עלות לפי מחירי ה-API: ${call['usd']:.4f}",
    ])


def terms_section(terms: list[dict]) -> str:
    """The terms shown during the call, written by the engine and not the model, each with what we already know
    about it from the knowledge files."""
    if not terms:
        return ""
    rows = []
    for t in terms:
        rows.append(f"- {t['term']}: {t['note']}")
        rows += [f"  - שלנו: {o}" for o in t.get("ours", ())]
    return "## מושגים שעלו\n" + "\n".join(rows) + "\n\n"


def write_terms(folder: Path, terms: list[dict]) -> Path:
    """Every term shown during the call, in the order it came, so the main window can offer the names that are not
    known yet; a term explained during the call has its detail. The file is replaced whole, so a reader never sees
    half of it."""
    path = folder / "terms.json"
    tmp = folder / "terms.json.tmp"
    rows = [{**{k: t[k] for k in TERM_FIELDS}, **({"detail": t["detail"]} if t.get("detail") else {})} for t in terms]
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def summarize(provider, folder: Path, prep: Prep, goals: list[Goal], *, on_usage: Callable,
              usage_text: Callable[[], str] = lambda: "", address: str = "", terms: list[dict] = (),
              with_usage: bool = True) -> SummaryResult:
    """summary.md, from the strong model or else locally; the result says which form it took (call or lecture).
    Without `with_usage` the file has no usage section."""
    kind = summary_kind(_call(folder)[0])
    body, ok, message = None, False, "no agent"
    if provider is not None:
        try:
            result = provider.summarize(LECTURE_RULES if kind == "lecture" else SUMMARY_RULES,
                                        build_input(folder, prep, goals, address))
            on_usage(result.model, result.usage)
            text = result.data if isinstance(result.data, str) else ""
            if text.strip():
                body, ok, message = text.strip(), True, ""
            else:
                message = "the summary reply was empty"
        except Exception as e:
            message = str(e) or type(e).__name__
    if body is None:
        body = local_summary(folder, goals, message).strip()
    path = folder / "summary.md"
    usage = f"## שימוש\n{usage_text() or 'אין נתוני שימוש'}\n" if with_usage else ""
    path.write_text(f"{body}\n\n{terms_section(list(terms))}{usage}".rstrip("\n") + "\n", encoding="utf-8")
    return SummaryResult(ok, path, message, kind)


def summarize_folder(data: Path, folder: Path, provider) -> SummaryResult:
    """The summary of a finished call, later: from its transcript and what its meta.json and terms.json kept (title,
    brief name, call type, goals, and `notes`, the text written for the call plus the notes added during it) and
    me.md. Writes summary.md without a usage section, which leaves usage.json as it is, and summary_kind and
    summary_local (true when the model failed and the summary was written locally) as the end of a call does. The brief file itself is not read, as at the end of a call: its text reaches the request only
    through `notes`."""
    from callcoach.context import address_of
    from callcoach.store import update_meta

    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    brief, title = meta.get("brief"), meta.get("title")
    prep = Prep.from_command({
        "call_type": meta.get("call_type") or "general", "lang": "auto", "level": "normal",
        "text": meta["notes"] if isinstance(meta.get("notes"), str) else "",
        "brief": brief if isinstance(brief, str) else "", "length_min": None, "goals": [], "glossary": [],
        "name": folder.name, "title": title if isinstance(title, str) else "", "questions": []})
    goals = [Goal(g["id"], g["text"], bool(g.get("done"))) for g in meta.get("goals") or []
             if isinstance(g, dict) and isinstance(g.get("id"), str) and isinstance(g.get("text"), str)]
    try:
        terms = json.loads((folder / "terms.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        terms = []
    terms = [t for t in terms if isinstance(t, dict) and isinstance(t.get("term"), str)] \
        if isinstance(terms, list) else []
    result = summarize(provider, folder, prep, goals, on_usage=lambda model, usage: None,
                       address=address_of(data), terms=[{"note": "", **t} for t in terms], with_usage=False)
    update_meta(folder, summary_kind=result.kind, summary_local=not result.ok)
    return result
