import io
import json
from pathlib import Path

import pytest

from callcoach.protocol import (ACTIONS, ALL_SECTIONS, CARD_KINDS, COMMANDS, ERRORS, EVENTS, PROTOCOL, SECTIONS, Emitter,
                                ProtocolError, make_event, parse_command)

FIX = Path(__file__).parent / "protocol"


def fixtures(kind):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((FIX / kind).glob("*.json"))}


def test_every_event_and_command_has_a_fixture():
    assert set(fixtures("events")) == set(EVENTS)
    assert set(fixtures("commands")) == set(COMMANDS)


def test_event_fixtures_are_valid():
    for name, msg in fixtures("events").items():
        fields = {k: v for k, v in msg.items() if k not in ("type", "t")}
        assert make_event(msg["type"], **fields)["type"] == name


def test_command_fixtures_parse():
    for name, msg in fixtures("commands").items():
        assert parse_command(json.dumps(msg))["type"] == name


def test_a_recap_may_ask_for_what_is_new_only():
    assert parse_command('{"type": "recap", "since": true}')["since"] is True
    with pytest.raises(ProtocolError):
        parse_command('{"type": "recap", "since": "yes"}')


def test_a_start_may_ask_for_a_record_only_call():
    start = json.loads((FIX / "commands" / "start.json").read_text(encoding="utf-8"))
    assert parse_command(json.dumps({**start, "record_only": True}))["record_only"] is True
    with pytest.raises(ProtocolError):
        parse_command(json.dumps({**start, "record_only": "yes"}))


def test_missing_field_is_rejected():
    with pytest.raises(ProtocolError):
        make_event("line", id=1)


@pytest.mark.parametrize("raw", ["nope", "[]", '{"type": "fly"}', '{"type": "set_language"}',
                                 '{"type": "set_language", "lang": "fr"}', '{"type": "action", "id": "fly"}',
                                 '{"type": []}', '{"type": {}}'])
def test_bad_commands_are_rejected(raw):
    with pytest.raises(ProtocolError):
        parse_command(raw)


def test_emitter_writes_json_lines_and_the_log(tmp_path):
    out = io.StringIO()
    e = Emitter(out, log=tmp_path / "events.log")
    e.emit("hello", protocol=PROTOCOL, version="x")
    e.error("capture", "שלום")
    lines = out.getvalue().splitlines()
    assert [json.loads(l)["type"] for l in lines] == ["hello", "error"]
    assert "שלום" in lines[1] and json.loads(lines[1])["ends_call"] is False
    assert (tmp_path / "events.log").read_text(encoding="utf-8").splitlines() == lines


def test_emitter_keeps_logging_when_the_app_is_gone(tmp_path):
    class Gone(io.StringIO):
        def write(self, s):
            raise BrokenPipeError

    e = Emitter(Gone(), log=tmp_path / "events.log")
    e.emit("hello", protocol=PROTOCOL, version="x")
    e.emit("hello", protocol=PROTOCOL, version="x")
    assert len((tmp_path / "events.log").read_text(encoding="utf-8").splitlines()) == 2


def test_emitter_keeps_logging_when_stdout_is_closed(tmp_path):
    out = io.StringIO()
    out.close()
    e = Emitter(out, log=tmp_path / "events.log")
    e.emit("hello", protocol=PROTOCOL, version="x")
    assert len((tmp_path / "events.log").read_text(encoding="utf-8").splitlines()) == 1


def test_error_codes_say_whether_the_call_ends():
    assert ERRORS["auto_stop"] is True and ERRORS["engine"] is True and ERRORS["no_sources"] is True
    assert ERRORS["recording_stopped"] is False and ERRORS["transcription_stopped"] is False
    assert ERRORS["agent_unavailable"] is False


def test_unknown_error_codes_are_rejected():
    with pytest.raises(ProtocolError):
        make_event("error", code="nope", message="x", ends_call=False)


def test_error_fixture_matches_the_table():
    msg = fixtures("events")["error"]
    assert msg["ends_call"] is ERRORS[msg["code"]]


START = {"type": "start", "call_type": "sales", "lang": "he", "level": "normal", "text": "", "brief": "",
         "length_min": None, "goals": [], "glossary": [], "name": "x", "title": "", "questions": []}


@pytest.mark.parametrize("raw", [
    '{"type": "set_level", "level": "loud"}',
    '{"type": "set_visibility", "visible": "no"}',
    '{"type": "shown", "card_id": "4", "t": 1.0}',
    '{"type": "shown", "card_id": 4, "t": true}',
    '{"type": "action", "id": "hide"}',
    '{"type": "explain_term", "id": "3"}',
    '{"type": "explain_term", "id": true}',
    '{"type": "explain_term"}',
    '{"type": "explain_term", "id": 3, "more": "yes"}',
    '{"type": "set_hearing", "on": "yes"}',
    '{"type": "set_hearing"}',
    '{"type": "set_sections", "ask": 1, "terms": true}',
    '{"type": "set_sections", "ask": true}',
    '{"type": "prepare", "call_type": "sales", "lang": "he", "level": "normal", "text": "", "briefs": [], '
    '"length_min": null}',
    *(json.dumps({**START, **bad}) for bad in (
        {"call_type": ""}, {"lang": "fr"}, {"brief": ["acme.md"]}, {"length_min": -5}, {"goals": [{"id": 1}]},
        {"glossary": "SOC 2"}, {"replay": 5}, {"title": None}, {"questions": "מה?"}, {"questions": [1]},
        {"sections": True}, {"sections": {"ask": True}}, {"sections": {"ask": 1, "terms": True}},
        {"sections": {"ask": True, "terms": "no"}}, {"sections": None})),
    json.dumps({k: v for k, v in START.items() if k != "title"}),
    json.dumps({k: v for k, v in START.items() if k != "questions"}),
])
def test_bad_values_are_rejected(raw):
    with pytest.raises(ProtocolError):
        parse_command(raw)


def test_the_now_line_is_an_optional_section():
    assert ALL_SECTIONS == ("ask", "terms", "now")
    assert parse_command('{"type": "set_sections", "ask": true, "terms": true, "now": false}')["now"] is False
    assert "now" not in parse_command('{"type": "set_sections", "ask": true, "terms": true}')
    assert parse_command(json.dumps({**START, "sections": {"ask": True, "terms": True, "now": True}}))["sections"]["now"]
    for raw in ('{"type": "set_sections", "ask": true, "terms": true, "now": 1}',
                json.dumps({**START, "sections": {"ask": True, "terms": True, "now": "on"}})):
        with pytest.raises(ProtocolError):
            parse_command(raw)


def test_list_only_is_an_optional_field_of_the_sections():
    assert parse_command('{"type": "set_sections", "ask": false, "terms": true, "list_only": true}')["list_only"] is True
    assert parse_command(json.dumps({**START, "sections": {"ask": False, "terms": True, "list_only": True}}))
    for raw in ('{"type": "set_sections", "ask": true, "terms": true, "list_only": "yes"}',
                json.dumps({**START, "sections": {"ask": True, "terms": True, "list_only": 1}})):
        with pytest.raises(ProtocolError):
            parse_command(raw)


def test_start_takes_sections_like_set_sections():
    msg = parse_command(json.dumps({**START, "sections": {"ask": False, "terms": True}}))
    assert msg["sections"] == {"ask": False, "terms": True}
    assert "sections" not in parse_command(json.dumps(START))


def test_start_and_set_language_take_auto():
    assert parse_command(json.dumps({**START, "lang": "auto"}))["lang"] == "auto"
    assert parse_command('{"type": "set_language", "lang": "auto"}')["lang"] == "auto"


def test_the_protocol_is_version_6_with_hearing_sections_asked_and_recap():
    assert PROTOCOL == 6 and "prepare" not in COMMANDS and "prep_result" not in EVENTS
    assert "prepare_failed" not in ERRORS
    assert COMMANDS["explain_term"] == ("id",) and EVENTS["term_detail"] == ("id", "ok", "text")
    assert COMMANDS["set_hearing"] == ("on",) and COMMANDS["set_sections"] == ("ask", "terms")
    assert COMMANDS["recap"] == () and EVENTS["recap"] == ("ok", "text")
    assert EVENTS["terms_now"] == ("ok", "ids", "picked") and ACTIONS == ("ask_now", "mark", "terms_now")
    assert COMMANDS["check_terms"] == ("terms",) and COMMANDS["add_screen"] == ("path", "text")
    assert EVENTS["screen"] == ("ok", "text")
    assert EVENTS["asked"] == ("text", "he", "trigger_line_ids")
    assert "hearing" in EVENTS["status"] and "me_voice" in EVENTS["status"] and "he" in EVENTS["card"]
    assert "insight" not in CARD_KINDS
    assert fixtures("events")["hello"]["protocol"] == PROTOCOL


def test_the_new_commands_parse():
    assert parse_command('{"type": "set_hearing", "on": false}')["on"] is False
    assert parse_command('{"type": "explain_term", "id": 3, "more": true}')["more"] is True
    assert "more" not in parse_command('{"type": "explain_term", "id": 3}')
    assert EVENTS["term"][-1] == "detail"
    assert parse_command('{"type": "set_sections", "ask": false, "terms": true}')["ask"] is False
    assert parse_command('{"type": "recap"}') == {"type": "recap"}


def test_start_may_name_a_replay_file():
    msg = dict(fixtures("commands")["start"], replay="demo/out/demo.wav")
    assert parse_command(json.dumps(msg))["replay"] == "demo/out/demo.wav"


def test_card_kind_is_not_the_message_type():
    assert "kind" in EVENTS["card"] and "type" not in EVENTS["card"]


def test_nothing_reaches_the_app_after_stopped(tmp_path):
    out = io.StringIO()
    e = Emitter(out, log=tmp_path / "events.log")
    e.emit("stopped")
    e.error("bad_command", "late")
    e.emit("stopped")
    assert [json.loads(l)["type"] for l in out.getvalue().splitlines()] == ["stopped"]
    assert len((tmp_path / "events.log").read_text(encoding="utf-8").splitlines()) == 3
    assert e.stopped


def test_notes_go_to_the_log_only(tmp_path):
    out = io.StringIO()
    e = Emitter(out, log=tmp_path / "events.log")
    e.note("echo_dropped", segment_id=3, score=0.91)
    assert out.getvalue() == ""
    note = json.loads((tmp_path / "events.log").read_text(encoding="utf-8"))
    assert note["type"] == "note" and note["kind"] == "echo_dropped" and note["score"] == 0.91 and "t" in note


def test_the_log_can_move(tmp_path):
    e = Emitter(io.StringIO(), log=tmp_path / "a.log")
    e.note("x")
    e.set_log(tmp_path / "b.log")
    e.note("y")
    assert (tmp_path / "a.log").exists() and (tmp_path / "b.log").exists()


@pytest.mark.parametrize("raw", [
    '{"type": "check_terms", "terms": []}',
    '{"type": "check_terms", "terms": ["a", "b", "c", "d", "e", "f"]}',
    '{"type": "check_terms", "terms": ["  "]}',
    '{"type": "check_terms", "terms": "Okta"}',
    '{"type": "add_screen", "path": "/x/screen.gif", "text": ""}',
    '{"type": "add_screen", "path": "/x/screen.png", "text": 3}',
])
def test_picked_terms_and_screenshots_are_checked(raw):
    with pytest.raises(ProtocolError):
        parse_command(raw)


def test_protocol_6_has_the_coach_the_goals_and_two_sections():
    assert PROTOCOL == 6
    assert SECTIONS == ("ask", "terms")
    assert parse_command('{"type": "set_sections", "ask": true, "terms": false}')["terms"] is False
    assert parse_command('{"type": "set_coach", "on": true}')["on"] is True
    assert parse_command('{"type": "set_goal", "id": "g1", "done": true}')["done"] is True
    assert parse_command('{"type": "focus_goal", "id": null}')["id"] is None
    assert parse_command('{"type": "focus_goal", "id": "g2"}')["id"] == "g2"
    for raw in ('{"type": "set_sections", "ask": true}', '{"type": "set_coach", "on": 1}',
                '{"type": "set_goal", "id": "", "done": true}', '{"type": "set_goal", "id": "g1", "done": "x"}',
                '{"type": "focus_goal", "id": 3}', '{"type": "focus_goal"}'):
        with pytest.raises(ProtocolError):
            parse_command(raw)


def test_stop_says_whether_to_summarize():
    assert parse_command('{"type": "stop", "summary": false}')["summary"] is False
    for raw in ('{"type": "stop"}', '{"type": "stop", "summary": "yes"}'):
        with pytest.raises(ProtocolError):
            parse_command(raw)


def test_the_status_says_whether_transcription_is_ready_and_the_topic_event_is_back():
    assert "transcribing" in EVENTS["status"] and EVENTS["topic"] == ("text", "trigger_line_ids")
    assert ERRORS["no_agent_account"] is False
