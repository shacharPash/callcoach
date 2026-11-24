"""Latency and language report for a replay of the demo call.

usage: uv run python demo/report.py CALL_FOLDER [TRUTH_JSON]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def load_events(folder: Path) -> list[dict]:
    text = (folder / "events.log").read_text(encoding="utf-8")
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def build_report(events: list[dict], truth: list[dict]) -> dict:
    t0 = next(e for e in events if e["type"] == "ready")["replay_t0"]
    lines = [e for e in events if e["type"] == "line"]
    segments = {e["id"]: e for e in events if e["type"] == "segment"}
    rows = []
    for u in truth:
        start, end = t0 + u["start"], t0 + u["end"]
        hits = sorted((l for l in lines if l["channel"] == u["who"] and l["t_start"] < end and l["speech_end"] > start),
                      key=lambda l: l["t_start"])
        if not hits:
            rows.append({**u, "got": None})
            continue
        last = max(hits, key=lambda l: l["line_ready"])
        seg = segments.get(last["segment_id"])
        rows.append({**u, "got": " ".join(h["text"] for h in hits), "got_lang": last["lang"],
                     "conversation_lang": last["conversation_lang"],
                     "endpoint": round(seg["closed_at"] - end, 3) if seg else None,
                     "line_latency": round(last["line_ready"] - end, 3)})
    found = [r for r in rows if r["got"] is not None]
    lat = [r["line_latency"] for r in found]
    summary = {
        "utterances": len(rows),
        "transcribed": len(found),
        "line_latency_median": round(statistics.median(lat), 3) if lat else None,
        "line_latency_worst": round(max(lat), 3) if lat else None,
        "language_correct": sum(r["got_lang"] == r["lang"] for r in found),
        "switch": switch_delay(rows),
    }
    return {"summary": summary, "rows": rows}


def switch_delay(rows: list[dict]) -> int | None:
    them = [r for r in rows if r["who"] == "them" and r["got"] is not None]
    first_en = next((i for i, r in enumerate(them) if r["lang"] == "en"), None)
    if first_en is None:
        return None
    for k, r in enumerate(them[first_en:], start=1):
        if r["conversation_lang"] == "en":
            return k
    return None


def to_markdown(report: dict) -> str:
    s = report["summary"]
    out = [
        "# Replay report", "",
        f"- Utterances transcribed: {s['transcribed']} of {s['utterances']}",
        f"- End of speech to line: median {s['line_latency_median']} s, worst {s['line_latency_worst']} s",
        f"- Language correct: {s['language_correct']} of {s['transcribed']}",
        f"- English 'them' utterances until the conversation language switched: {s['switch']}", "",
        "| # | who | lang | got | line s | endpoint s | said | transcribed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        out.append(f"| {r['i']} | {r['who']} | {r['lang']} | {r.get('got_lang', '')} | {r.get('line_latency', '')} "
                   f"| {r.get('endpoint', '')} | {r['text']} | {r['got'] or 'MISSING'} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> None:
    folder = Path(argv[1])
    truth_path = Path(argv[2]) if len(argv) > 2 else Path(__file__).resolve().parent / "out" / "demo.truth.json"
    report = build_report(load_events(folder), json.loads(truth_path.read_text(encoding="utf-8")))
    md = to_markdown(report)
    (folder / "latency.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main(sys.argv)
