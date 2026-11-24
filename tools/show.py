"""Readable live transcript from the engine's events: bin/callcoach-engine run ... | .venv/bin/python tools/show.py"""
from __future__ import annotations

import json
import signal
import sys

NAMES = {"me": "אני", "them": "הצד השני"}


def show(event: dict) -> str | None:
    kind = event.get("type")
    if kind == "ready":
        return "מוכן, מקשיב. לסיום: Ctrl+C"
    if kind == "line":
        return f"{NAMES.get(event['channel'], event['channel'])}: {event['text']}"
    if kind == "error":
        return f"שגיאה ({event.get('code')}): {event.get('message')}"
    if kind == "finalized":
        return "ההקלטה נשמרה" if event.get("ok") else f"ההקלטה לא נסגרה: {event.get('reason')}"
    return None


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for raw in sys.stdin:
        try:
            text = show(json.loads(raw))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if text:
            print(text, flush=True)


if __name__ == "__main__":
    main()
