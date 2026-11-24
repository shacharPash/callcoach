import json
from pathlib import Path

from callcoach.protocol import ERRORS, EVENTS, PROTOCOL, make_event

STREAM = Path(__file__).parent / "protocol" / "stream" / "demo-call.jsonl"


def events():
    return [json.loads(l) for l in STREAM.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_every_event_in_the_stream_is_valid():
    for e in events():
        fields = {k: v for k, v in e.items() if k not in ("type", "t")}
        make_event(e["type"], **fields)


def test_the_stream_covers_every_event_type():
    assert {e["type"] for e in events()} == set(EVENTS)


def test_the_stream_order_rules_hold():
    es = events()
    assert es[0]["type"] == "hello" and es[0]["protocol"] == PROTOCOL
    assert es[-1]["type"] == "stopped" and [e["type"] for e in es].count("stopped") == 1
    times = [e["t"] for e in es]
    assert times == sorted(times)


def test_cards_point_at_earlier_lines_and_errors_match_the_table():
    seen, card_ids = set(), []
    for e in events():
        if e["type"] == "line":
            seen.add(e["id"])
        if e["type"] == "card":
            assert set(e["trigger_line_ids"]) <= seen
            card_ids.append(e["id"])
        if e["type"] == "error":
            assert e["ends_call"] is ERRORS[e["code"]]
    assert card_ids == sorted(set(card_ids))
