import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))

from report import build_report, to_markdown  # noqa: E402

T0 = 1000.0
TRUTH = [
    {"i": 0, "who": "them", "lang": "he", "text": "א", "start": 1.0, "end": 2.0, "moment": None},
    {"i": 1, "who": "them", "lang": "en", "text": "b", "start": 3.0, "end": 4.0, "moment": "switch"},
    {"i": 2, "who": "them", "lang": "en", "text": "c", "start": 5.0, "end": 6.0, "moment": None},
    {"i": 3, "who": "me", "lang": "en", "text": "d", "start": 7.0, "end": 8.0, "moment": None},
]


def seg(id, end):
    return {"type": "segment", "id": id, "channel": "them", "t_start": T0 + end - 1, "speech_end": T0 + end,
            "closed_at": T0 + end + 0.6, "duration": 1.4}


def line(id, end, lang, conv, latency):
    return {"type": "line", "id": id, "segment_id": id, "channel": "them", "lang": lang, "text": f"t{id}",
            "t_start": T0 + end - 1, "speech_end": T0 + end, "line_ready": T0 + end + latency,
            "conversation_lang": conv}


EVENTS = [{"type": "ready", "folder": "x", "replay_t0": T0},
          seg(1, 2.0), line(1, 2.0, "he", "he", 1.0),
          seg(2, 4.0), line(2, 4.0, "en", "he", 1.2),
          seg(3, 6.0), line(3, 6.0, "en", "en", 1.6)]


def test_report_summary():
    s = build_report(EVENTS, TRUTH)["summary"]
    assert (s["utterances"], s["transcribed"]) == (4, 3)
    assert s["line_latency_median"] == 1.2 and s["line_latency_worst"] == 1.6
    assert s["language_correct"] == 3
    assert s["switch"] == 2


def test_missing_utterances_are_listed():
    rows = build_report(EVENTS, TRUTH)["rows"]
    assert rows[3]["got"] is None
    assert "MISSING" in to_markdown(build_report(EVENTS, TRUTH))
