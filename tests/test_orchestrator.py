import io
import json
import threading
import time

from callcoach.cards import Question
from callcoach.context import CallType, Goal, parse_call_type
from callcoach.data import Line, Segment
from callcoach.knowledge import Entry, Knowledge, parse
from callcoach.meter import TIP as METER_TIP, Meter
from callcoach.orchestrator import NOW_HOLD_S, QUESTIONS_HOLD_S, Orchestrator
from callcoach.protocol import Emitter
from callcoach.providers import FakeProvider, ProviderAuth, ProviderUnavailable

CALL = CallType("validation", cards=("question", "tip", "steer", "term"))
LISTEN = CallType("listen", cards=("tip",))
TIP = {"card": {"kind": "tip", "text": "מחמאה, לא עובדה.", "lang": "he"}, "goals": []}
QUESTION = {"card": {"kind": "question", "text": "מי מחליט?", "lang": "he"}, "goals": []}
NONE = {"card": None, "goals": []}
LONG = "זה לוקח לנו יומיים בערך"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def line(i, text=LONG, channel="them", t=1000.0, lang="he"):
    return Line(i, i, channel, lang, text, t, t + 1.0, t + 1.2)


def seg(channel, seconds, end):
    return Segment(1, channel, bytes(int(seconds * 32000)), end - seconds, end, end)


def make(replies=None, *, call_type=CALL, errors=None, clock=None, meter=None, level="normal", provider="fake",
         log=None, card_gap_s=0.0, knowledge=None, questions=(), brief_terms=None, title="", call_notes="",
         ignored=(), coach=True):
    clock = clock or Clock()
    out = io.StringIO()
    if provider == "fake":
        provider = FakeProvider(replies if replies is not None else [NONE] * 10, errors=errors)
    used = []
    orch = Orchestrator(provider=provider, emitter=Emitter(out, log=log), system="rules", call_type=call_type,
                        goals=(Goal("g1", "מי מחליט"), Goal("g2", "איך עושים היום")), level=level, lang="he",
                        started=clock.t, length_min=30, meter=meter, on_usage=lambda model, usage: used.append(model),
                        clock=clock, card_gap_s=card_gap_s, knowledge=knowledge, questions=questions,
                        brief_terms=brief_terms, title=title, call_notes=call_notes, ignored=ignored,
                        coach=coach)
    if coach:
        orch.open()
    return orch, provider, out, clock, used


def events(out, kind):
    return [e for e in map(json.loads, out.getvalue().splitlines()) if e["type"] == kind]


def notes(log, kind):
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["type"] == "note" and r["kind"] == kind]


def sent(fake, i=0):
    return [m for kind, m in fake.calls if kind == "step"][i]


def texts(event):
    return [q["text"] for q in event["items"]]


def test_a_them_line_of_three_words_sends_one_request():
    orch, fake, out, clock, used = make()
    orch.on_line(line(1, "איך אתם עושים את זה היום"), "he")
    assert orch.pump() is True and orch.pump() is False
    message = sent(fake)
    for part in ("[00:00] them (he): איך אתם עושים את זה היום", "mode: normal", "card_language: he",
                 "planned length: 30 min", "- [g1] open: מי מחליט", "shown_cards:"):
        assert part in message
    assert used == ["fake"] and fake.system == "rules"


def test_short_lines_wait_for_the_next_request():
    orch, fake, *_ = make()
    orch.on_line(line(1, "כן"), "he")
    orch.on_line(line(2, "אני מבין", channel="me"), "he")
    assert orch.pump() is False and fake.calls == []
    orch.on_line(line(3), "he")
    assert orch.pump() is True
    assert "כן" in sent(fake) and "אני מבין" in sent(fake) and "יומיים" in sent(fake)


def test_my_lines_trigger_only_at_the_active_level():
    normal, *_ = make()
    normal.on_line(line(1, "מתי זה קרה בפעם האחרונה", channel="me"), "he")
    assert normal.pump() is False
    active, *_ = make(level="active")
    active.on_line(line(1, "מתי זה קרה בפעם האחרונה", channel="me"), "he")
    assert active.pump() is True


def test_a_card_carries_its_trigger_and_times():
    orch, fake, out, *_ = make([TIP])
    orch.on_line(line(7, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.pump()
    [card] = events(out, "card")
    assert (card["id"], card["kind"], card["lang"], card["trigger_line_ids"], card["forced"]) == \
        (1, "tip", "he", [7], False)
    assert card["requested_at"] == 1000.0 and card["received_at"] == 1000.0


def test_a_reply_older_than_eight_seconds_is_dropped(tmp_path):
    clock = Clock()

    def slow(message):
        clock.t += 10.0
        return TIP

    orch, fake, out, *_ = make(slow, clock=clock, log=tmp_path / "events.log")
    orch.on_line(line(1, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.pump()
    assert events(out, "card") == []
    assert notes(tmp_path / "events.log", "agent_stale")[0]["card_kind"] == "tip"


def test_hidden_sends_nothing_and_showing_sends_one_catch_up():
    orch, fake, *_ = make()
    orch.set_visibility(False)
    orch.on_line(line(1), "he")
    orch.on_line(line(2, "ואז מישהו מאשר את זה"), "he")
    assert orch.pump() is False
    orch.set_visibility(True)
    assert orch.pump() is True and orch.pump() is False
    assert "mode: catch_up" in sent(fake) and "ואז מישהו מאשר את זה" in sent(fake)
    assert orch.status() == {"agent": "ok", "level": "normal", "visible": True}


def test_showing_with_nothing_new_sends_nothing():
    orch, fake, *_ = make()
    orch.set_visibility(False)
    orch.set_visibility(True)
    assert orch.pump() is False


def test_ask_now_forces_a_question_even_without_new_lines(tmp_path):
    orch, fake, out, clock, _ = make([TIP, QUESTION], log=tmp_path / "events.log")
    orch.ask_now(clock.t)
    orch.pump()
    assert "mode: forced_question" in sent(fake) and "## New lines\n(none)" in sent(fake)
    assert events(out, "card") == [] and notes(tmp_path / "events.log", "reply_problem")
    orch.ask_now(clock.t)
    orch.pump()
    [card] = events(out, "card")
    assert card["kind"] == "question" and card["forced"] is True


def test_the_quiet_level_only_updates_goals():
    reply = {"card": {"kind": "tip", "text": "x", "lang": "he"}, "goals": [{"id": "g1", "done": True}]}
    orch, fake, out, *_ = make([reply], level="quiet")
    orch.on_line(line(1, "אני זאת שמחליטה על זה"), "he")
    orch.pump()
    assert "mode: goals_only" in sent(fake)
    assert events(out, "card") == []
    assert events(out, "goals")[0]["goals"][0] == {"id": "g1", "text": "מי מחליט", "done": True}


def test_goal_updates_apply_once_and_only_to_known_goals(tmp_path):
    reply = {"card": None, "goals": [{"id": "g1", "done": True}, {"id": "zz", "done": True}]}
    orch, fake, out, *_ = make([reply, reply], log=tmp_path / "events.log")
    for i in (1, 2):
        orch.on_line(line(i, "אני זאת שמחליטה על זה"), "he")
        orch.pump()
    assert len(events(out, "goals")) == 1
    assert notes(tmp_path / "events.log", "unknown_goal")
    assert [g.done for g in orch.goal_list()] == [True, False]


def test_unavailable_is_reported_once_keeps_the_lines_and_retries_later():
    orch, fake, out, clock, _ = make([QUESTION], errors=[ProviderUnavailable("busy")])
    orch.on_line(line(1), "he")
    orch.pump()
    assert orch.status()["agent"] == "unavailable"
    orch.on_line(line(2, "ואז מישהו מאשר את זה"), "he")
    clock.t += 5
    assert orch.pump() is False
    clock.t += 11
    assert orch.pump() is True
    assert "mode: catch_up" in sent(fake, 1) and "יומיים" in sent(fake, 1) and "מאשר" in sent(fake, 1)
    assert orch.status()["agent"] == "ok"
    assert [e["code"] for e in events(out, "error")] == ["agent_unavailable"]
    assert len(events(out, "card")) == 1


def test_an_auth_error_stops_all_requests():
    orch, fake, out, *_ = make(errors=[ProviderAuth("please log in")])
    orch.on_line(line(1), "he")
    orch.pump()
    orch.on_line(line(2), "he")
    orch.ask_now(1000.0)
    assert orch.pump() is False and len(fake.calls) == 1
    assert orch.status()["agent"] == "auth"
    assert [e["code"] for e in events(out, "error")] == ["agent_auth"]


def test_a_failed_open_is_unavailable_and_is_retried():
    class Flaky(FakeProvider):
        opens = 0

        def open_call(self, system, schema):
            Flaky.opens += 1
            if Flaky.opens == 1:
                raise ProviderUnavailable("starting")
            super().open_call(system, schema)

    orch, fake, out, clock, _ = make(provider=Flaky([NONE]))
    assert orch.status()["agent"] == "unavailable"
    orch.on_line(line(1), "he")
    clock.t += 16
    assert orch.pump() is True and Flaky.opens == 2 and orch.status()["agent"] == "ok"


def test_the_local_tip_comes_from_the_meter_without_a_request():
    clock = Clock()
    orch, fake, out, *_ = make(clock=clock, meter=Meter())
    t = clock.t
    for i in range(20):
        orch.on_segment(seg("me", 9.0, t + i * 10 + 9))
        orch.on_segment(seg("them", 1.0, t + i * 10 + 10))
    clock.t = t + 200
    assert orch.pump() is False
    [card] = events(out, "card")
    assert (card["kind"], card["text"], card["lang"], card["he"], card["trigger_line_ids"]) == ("tip", METER_TIP, "he", "", [])
    assert fake.calls == []


def test_the_local_tip_waits_while_callcoach_does_not_hear_me():
    clock = Clock()
    orch, fake, out, *_ = make(clock=clock, meter=Meter())
    t = clock.t
    for i in range(20):
        orch.on_segment(seg("me", 9.0, t + i * 10 + 9))
        orch.on_segment(seg("them", 1.0, t + i * 10 + 10))
    orch.set_hearing(False)
    clock.t = t + 200
    orch.pump()
    assert events(out, "card") == []
    orch.set_hearing(True)
    orch.pump()
    assert [c["kind"] for c in events(out, "card")] == ["tip"]


def test_shown_cards_are_in_the_next_message():
    orch, fake, out, *_ = make([TIP, NONE])
    orch.on_line(line(1, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.pump()
    orch.shown(1, 1000.5)
    orch.on_line(line(2), "he")
    orch.pump()
    assert "- [shown] tip: מחמאה, לא עובדה." in sent(fake, 1)


def test_a_reply_that_is_not_json_is_logged_not_shown(tmp_path):
    orch, fake, out, *_ = make(["sorry, no"], log=tmp_path / "events.log")
    orch.on_line(line(1), "he")
    orch.pump()
    assert events(out, "card") == [] and notes(tmp_path / "events.log", "bad_reply")


def test_without_a_provider_the_agent_is_off():
    orch, *_ = make(provider=None)
    orch.on_line(line(1), "he")
    assert orch.pump() is False
    assert orch.status() == {"agent": "off", "level": "normal", "visible": True}


def test_the_thread_sends_one_request_at_a_time():
    active, peak, lock = [0], [0], threading.Lock()

    def reply(message):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return NONE

    fake = FakeProvider(reply)
    orch = Orchestrator(provider=fake, emitter=Emitter(io.StringIO()), system="rules", call_type=CALL,
                        started=time.time())
    orch.start()
    for i in range(6):
        orch.on_line(line(i + 1, t=time.time()), "he")
        time.sleep(0.01)
    time.sleep(0.5)
    orch.close()
    messages = [m for kind, m in fake.calls if kind == "step"]
    assert peak[0] == 1 and 1 <= len(messages) <= 6
    assert sum(m.count(LONG) for m in messages) == 6


def wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def test_a_reply_whose_goals_are_not_a_list_is_a_bad_reply(tmp_path):
    orch, fake, out, *_ = make([{"card": None, "goals": 5}, TIP], log=tmp_path / "events.log")
    orch.on_line(line(1), "he")
    orch.pump()
    orch.on_line(line(2, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.pump()
    assert notes(tmp_path / "events.log", "bad_reply") and len(events(out, "card")) == 1


def test_an_unexpected_error_is_a_note_and_the_thread_goes_on(tmp_path):
    log, out, used = tmp_path / "events.log", io.StringIO(), []

    def on_usage(model, usage):
        used.append(model)
        if len(used) == 1:
            raise RuntimeError("usage bug")

    fake = FakeProvider([NONE, TIP])
    orch = Orchestrator(provider=fake, emitter=Emitter(out, log=log), system="rules", call_type=CALL,
                        started=time.time(), on_usage=on_usage, poll_s=0.01)
    orch.start()
    orch.on_line(line(1, t=time.time()), "he")
    assert wait_for(lambda: len(fake.calls) == 1)
    orch.on_line(line(2, "בטוח הייתי משתמש בכלי כזה", t=time.time()), "he")
    assert wait_for(lambda: events(out, "card"))
    alive = orch.thread.is_alive()
    orch.close()
    [note] = notes(log, "orchestrator_error")
    assert note["error"] == "RuntimeError" and note["message"] == "usage bug" and alive


def test_quiet_shows_no_catch_up_card():
    orch, fake, out, *_ = make([TIP], level="quiet")
    orch.set_visibility(False)
    orch.on_line(line(1, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.set_visibility(True)
    assert orch.pump() is True and "mode: catch_up" in sent(fake)
    assert events(out, "card") == []


def test_quiet_set_while_a_request_runs_drops_its_card():
    orch = None

    def reply(message):
        orch.set_level("quiet")
        return TIP

    orch, fake, out, *_ = make(reply)
    orch.on_line(line(1, "בטוח הייתי משתמש בכלי כזה"), "he")
    orch.pump()
    assert events(out, "card") == []


def test_quiet_still_answers_ask_now():
    orch, fake, out, clock, _ = make([QUESTION], level="quiet")
    orch.ask_now(clock.t)
    orch.pump()
    [card] = events(out, "card")
    assert card["kind"] == "question" and card["forced"] is True


def test_the_rules_say_what_quiet_means():
    from callcoach.prompts import STEP_RULES

    assert "level quiet" in STEP_RULES


def test_ask_now_during_the_backoff_is_sent_at_once():
    orch, fake, out, clock, _ = make([QUESTION], errors=[ProviderUnavailable("busy")])
    orch.on_line(line(1), "he")
    orch.pump()
    clock.t += 2
    orch.ask_now(clock.t)
    assert orch.pump() is True and "mode: forced_question" in sent(fake, 1)
    [card] = events(out, "card")
    assert card["forced"] is True and orch.status()["agent"] == "ok"


def test_a_forced_request_that_fails_stays_forced():
    orch, fake, out, clock, _ = make([QUESTION], errors=[ProviderUnavailable("busy")])
    orch.on_line(line(1), "he")
    orch.ask_now(clock.t)
    orch.pump()
    clock.t += 1
    assert orch.pump() is True
    assert "mode: forced_question" in sent(fake, 1) and "יומיים" in sent(fake, 1)
    [card] = events(out, "card")
    assert card["forced"] is True


def test_a_forced_request_bypasses_the_backoff_only_once(tmp_path):
    busy = ProviderUnavailable("busy")
    orch, fake, out, clock, _ = make([NONE], errors=[busy, busy, busy], log=tmp_path / "events.log")
    orch.on_line(line(1), "he")
    orch.pump()
    clock.t += 1
    orch.ask_now(clock.t)
    assert orch.pump() is True
    clock.t += 1
    assert orch.pump() is False
    clock.t += 16
    assert orch.pump() is True and "mode: catch_up" in sent(fake, 2) and "יומיים" in sent(fake, 2)
    assert notes(tmp_path / "events.log", "forced_expired")


def test_provider_notes_reach_the_log(tmp_path):
    class Noisy(FakeProvider):
        def open_call(self, system, schema):
            self.notes.add("provider_disconnect_failed", error="RuntimeError", message="at open")
            super().open_call(system, schema)

        def step(self, message):
            self.notes.add("provider_disconnect_failed", error="RuntimeError", message="at step")
            return super().step(message)

    log = tmp_path / "events.log"
    orch, *_ = make(provider=Noisy([NONE], errors=[ProviderUnavailable("busy")]), log=log)
    orch.on_line(line(1), "he")
    orch.pump()
    assert [n["message"] for n in notes(log, "provider_disconnect_failed")] == ["at open", "at step"]


def test_context_i_add_is_in_every_next_message():
    orch, fake, *_ = make()
    orch.add_context("  דנה מנהלת IT ב-Acme  ")
    orch.on_line(line(1), "he")
    orch.pump()
    orch.on_line(line(2), "he")
    orch.pump()
    for i in (0, 1):
        assert "my_notes:\n- דנה מנהלת IT ב-Acme\n" in sent(fake, i)
    assert orch.context_notes() == ["דנה מנהלת IT ב-Acme"]


def test_no_notes_means_no_notes_section():
    orch, fake, *_ = make()
    orch.on_line(line(1), "he")
    orch.pump()
    assert "my_notes" not in sent(fake)


def test_a_card_waits_a_gap_after_the_last_one():
    clock = Clock()
    orch, fake, out, *_ = make([TIP, QUESTION, QUESTION], clock=clock, card_gap_s=60)
    orch.on_line(line(1), "he")
    orch.pump()
    clock.t += 20
    orch.on_line(line(2, t=clock.t), "he")
    orch.pump()
    assert "mode: goals_only" in sent(fake, 1)
    assert [c["kind"] for c in events(out, "card")] == ["tip"]
    clock.t += 45
    orch.on_line(line(3, t=clock.t), "he")
    orch.pump()
    assert "mode: normal" in sent(fake, 2)
    assert [c["kind"] for c in events(out, "card")] == ["tip", "question"]


def test_ask_now_ignores_the_gap():
    clock = Clock()
    orch, fake, out, *_ = make([TIP, QUESTION], clock=clock, card_gap_s=60)
    orch.on_line(line(1), "he")
    orch.pump()
    clock.t += 5
    orch.ask_now(clock.t)
    orch.pump()
    assert [(c["kind"], c["forced"]) for c in events(out, "card")] == [("tip", False), ("question", True)]


def test_the_active_level_halves_the_gap():
    clock = Clock()
    orch, fake, out, *_ = make([TIP, QUESTION], clock=clock, card_gap_s=60, level="active")
    orch.on_line(line(1), "he")
    orch.pump()
    clock.t += 35
    orch.on_line(line(2, t=clock.t), "he")
    orch.pump()
    assert [c["kind"] for c in events(out, "card")] == ["tip", "question"]


def test_without_nudges_terms_still_come_and_ask_now_still_answers():
    term = {"card": TIP["card"], "goals": [], "terms": [{"term": "SOC 2", "note": "תקן ביקורת אבטחה"}]}
    orch, fake, out, *_ = make([term, QUESTION], level="quiet")
    orch.goals = {}
    orch.on_line(line(1), "he")
    assert orch.pump() is True
    assert "mode: goals_only" in sent(fake)
    assert [t["term"] for t in events(out, "term")] == ["SOC 2"] and events(out, "card") == []
    orch.ask_now(orch.clock())
    assert orch.pump() is True
    assert "mode: forced_question" in sent(fake, 1)
    assert [c["kind"] for c in events(out, "card")] == ["question"]


def test_terms_show_once_and_the_agent_knows_them():
    first = {"card": None, "goals": [], "terms": [{"term": "SOC 2", "note": "תקן ביקורת"},
                                                  {"term": "Okta", "note": "התחברות לארגונים"}]}
    again = {"card": None, "goals": [], "terms": [{"term": "soc-2", "note": "שוב"},
                                                  {"term": "Nimbus Labs", "note": "כלי שקנו"}]}
    orch, fake, out, *_ = make([first, again])
    orch.on_line(line(1), "he")
    orch.pump()
    orch.on_line(line(2), "he")
    orch.pump()
    terms = events(out, "term")
    assert [(t["id"], t["term"]) for t in terms] == [(1, "SOC 2"), (2, "Okta"), (3, "Nimbus Labs")]
    assert terms[0]["note"] == "תקן ביקורת" and terms[0]["lang"] == "he" and terms[0]["trigger_line_ids"] == [1]
    assert "known_terms: (none)" in sent(fake, 0) and "known_terms: SOC 2, Okta" in sent(fake, 1)
    assert [t["term"] for t in orch.term_list()] == ["SOC 2", "Okta", "Nimbus Labs"]


def test_a_term_note_is_hebrew_in_an_english_call():
    reply = {"card": None, "goals": [], "terms": [{"term": "Okta", "note": "התחברות לארגונים"}]}
    orch, fake, out, *_ = make([reply])
    orch.on_line(line(1), "en")
    orch.pump()
    assert "card_language: en" in sent(fake) and events(out, "term")[0]["lang"] == "he"


def test_a_late_term_still_shows_but_a_late_card_does_not():
    clock = Clock()
    late = {"card": TIP["card"], "goals": [], "terms": [{"term": "SOC 2", "note": "תקן ביקורת"}]}
    orch, fake, out, *_ = make([late], clock=clock)
    orch.on_line(line(1, t=clock.t - 20), "he")
    orch.pump()
    assert [t["term"] for t in events(out, "term")] == ["SOC 2"] and events(out, "card") == []


def test_the_card_gap_does_not_hold_back_terms():
    clock = Clock()
    second = {"card": QUESTION["card"], "goals": [], "terms": [{"term": "Okta", "note": "התחברות"}]}
    orch, fake, out, *_ = make([TIP, second], clock=clock, card_gap_s=60)
    orch.on_line(line(1, t=clock.t), "he")
    orch.pump()
    clock.t += 5
    orch.on_line(line(2, t=clock.t), "he")
    orch.pump()
    assert [c["kind"] for c in events(out, "card")] == ["tip"]
    assert [t["term"] for t in events(out, "term")] == ["Okta"]


def test_a_term_card_from_an_old_call_type_is_not_shown():
    term_card = {"card": {"kind": "term", "text": "SOC 2: תקן", "lang": "he"}, "goals": []}
    orch, fake, out, *_ = make([term_card])
    orch.on_line(line(1), "he")
    orch.pump()
    assert events(out, "card") == []


def test_listening_only_with_goals_still_tracks_them():
    orch, fake, *_ = make(level="quiet")
    orch.on_line(line(1), "he")
    assert orch.pump() is True
    assert "mode: goals_only" in sent(fake)


ASK = {"card": None, "goals": [], "terms": [], "questions": ["מה היה הכי קשה?", "מי עוד מעורב?"]}


def test_live_questions_start_empty_then_follow_the_call():
    later = {"card": None, "goals": [], "terms": [], "questions": ["ומה התקציב?"]}
    clock = Clock()
    orch, fake, out, *_ = make([ASK, dict(ASK), {"card": None, "goals": [], "terms": []}, later], clock=clock)
    for i in range(1, 5):
        if i == 4:
            clock.t += QUESTIONS_HOLD_S
        orch.on_line(line(i, t=clock.t), "he")
        orch.pump()
    shown = [q["items"] for q in events(out, "questions")]
    assert shown == [[{"text": "מה היה הכי קשה?", "he": ""}, {"text": "מי עוד מעורב?", "he": ""}],
                     [{"text": "ומה התקציב?", "he": ""}]]
    assert events(out, "questions")[0]["trigger_line_ids"] == [1]
    assert "live_questions: on" in sent(fake, 0)
    assert "shown_questions: מה היה הכי קשה? | מי עוד מעורב?" in sent(fake, 1)


def test_live_questions_start_empty_when_the_call_starts():
    orch, fake, out, *_ = make()
    orch.start()
    orch.close()
    assert [(q["items"], q["lang"]) for q in events(out, "questions")] == [([], "he")]


def test_live_questions_come_without_nudges_and_through_the_card_gap():
    clock = Clock()
    orch, fake, out, *_ = make([TIP, ASK], clock=clock, card_gap_s=60, level="quiet")
    orch.on_line(line(1, t=clock.t), "he")
    orch.pump()
    clock.t += 5
    orch.on_line(line(2, t=clock.t), "he")
    orch.pump()
    assert events(out, "card") == [] and texts(events(out, "questions")[-1]) == ASK["questions"]


def test_late_live_questions_are_dropped():
    clock = Clock()
    orch, fake, out, *_ = make([ASK], clock=clock)
    orch.on_line(line(1, t=clock.t - 20), "he")
    orch.pump()
    assert events(out, "questions") == []


def test_a_call_type_without_question_cards_has_no_live_questions():
    orch, fake, out, *_ = make([ASK], call_type=LISTEN)
    orch.start()
    orch.close()
    orch.on_line(line(1), "he")
    orch.pump()
    assert events(out, "questions") == []


OLD_WATCH = parse_call_type("watch", "cadence: watch\ncards: term\ngoals:\n---\nI am only listening.\n")


def test_an_old_watch_file_listens_like_a_call():
    orch, fake, out, clock, _ = make([ASK, ASK], call_type=OLD_WATCH)
    orch.on_line(line(1, "a queue", lang="en"), "en")
    assert orch.pump() is False
    orch.on_line(line(2, "the key idea is backpressure", lang="en"), "en")
    assert orch.pump() is True
    assert "mode: normal" in sent(fake) and "live_questions: off" in sent(fake) and "shown_questions" not in sent(fake)
    assert events(out, "questions") == []


def test_shown_live_questions_stay_at_least_their_hold():
    second = {"card": None, "goals": [], "terms": [], "questions": ["ומה התקציב?"]}
    third = {"card": None, "goals": [], "terms": [], "questions": ["ומי חותם בסוף?"]}
    clock = Clock()
    orch, fake, out, *_ = make([ASK, second, third], clock=clock)
    for wait in (0, QUESTIONS_HOLD_S - 5, 10):
        clock.t += wait
        orch.on_line(line(1, t=clock.t), "he")
        orch.pump()
    assert [texts(e) for e in events(out, "questions")] == [ASK["questions"], third["questions"]]


def now_reply(text):
    return {"card": None, "goals": [], "terms": [], "questions": [], "now": text, "asked": None}


FIRST_NOW = "דנה מסבירה איך בקשות גישה עוברות היום במייל"


def test_a_new_now_line_is_a_topic_and_the_same_line_or_empty_sends_nothing():
    replies = [now_reply(FIRST_NOW), now_reply(""), now_reply(f" {FIRST_NOW}. "), now_reply("מדברים על מי מאשר גישה")]
    clock = Clock()
    orch, fake, out, *_ = make(replies, clock=clock)
    for i in range(1, 5):
        if i == 4:
            clock.t += NOW_HOLD_S
        orch.on_line(line(i, t=clock.t), "he")
        orch.pump()
    assert [(e["text"], e["trigger_line_ids"]) for e in events(out, "topic")] == \
        [(FIRST_NOW, [1]), ("מדברים על מי מאשר גישה", [4])]
    assert "sections: ask on, terms on, now on" in sent(fake, 0)
    assert "shown_now: (none)" in sent(fake, 0) and f"shown_now: {FIRST_NOW}" in sent(fake, 1)


def test_a_shown_now_line_stays_at_least_its_hold():
    later, last = "מדברים על התקציב", "מדברים על מי מאשר גישה"
    clock = Clock()
    orch, fake, out, *_ = make([now_reply(FIRST_NOW), now_reply(later), now_reply(last)], clock=clock)
    for wait in (0, NOW_HOLD_S - 5, 10):
        clock.t += wait
        orch.on_line(line(1, t=clock.t), "he")
        orch.pump()
    assert [e["text"] for e in events(out, "topic")] == [FIRST_NOW, last]
    assert f"shown_now: {FIRST_NOW}" in sent(fake, 1)


def test_the_now_line_comes_at_the_quiet_level():
    orch, fake, out, *_ = make([now_reply(FIRST_NOW)], level="quiet")
    orch.on_line(line(1), "he")
    orch.pump()
    assert "mode: goals_only" in sent(fake) and [e["text"] for e in events(out, "topic")] == [FIRST_NOW]


def test_a_late_now_line_is_dropped():
    clock = Clock()
    orch, fake, out, *_ = make([now_reply(FIRST_NOW)], clock=clock)
    orch.on_line(line(1, t=clock.t - 20), "he")
    orch.pump()
    assert events(out, "topic") == [] and "shown_now: (none)" in sent(fake)


def test_a_closed_now_line_is_not_asked_for_and_is_dropped():
    orch, fake, out, *_ = make([now_reply(FIRST_NOW)])
    orch.set_sections(True, True)
    orch.on_line(line(1), "he")
    assert orch.pump()
    assert not events(out, "topic") and "shown_now" not in sent(fake) and "now off" in sent(fake)


def test_closing_now_forgets_the_line_so_it_shows_again_when_opened():
    orch, fake, out, *_ = make([now_reply(FIRST_NOW), now_reply(FIRST_NOW)])
    orch.on_line(line(1), "he")
    orch.pump()
    orch.set_sections(True, True, False)
    orch.set_sections(True, True, True)
    orch.on_line(line(2), "he")
    orch.pump()
    assert "shown_now: (none)" in sent(fake, 1)
    assert [t["text"] for t in events(out, "topic")] == [FIRST_NOW, FIRST_NOW]


def test_the_now_line_alone_keeps_the_live_step_going():
    orch, fake, out, *_ = make([now_reply(FIRST_NOW)])
    orch.set_sections(False, False, True)
    orch.on_line(line(1), "he")
    assert orch.pump()
    assert "sections: ask off, terms off, now on" in sent(fake)
    assert [e["text"] for e in events(out, "topic")] == [FIRST_NOW]


LATE = ("מה גרם לכם לחפש פתרון עכשיו?", "איך זה עובד אצלכם היום?", "מי מאשר בקשה כזאת?")


def test_background_questions_show_when_nothing_was_shown_yet():
    orch, fake, out, *_ = make()
    orch.offer_questions(tuple(Question(q) for q in LATE))
    assert [(texts(q), q["lang"], q["trigger_line_ids"]) for q in events(out, "questions")] == [(list(LATE), "he", [])]
    orch.on_line(line(1), "he")
    orch.pump()
    assert "shown_questions: " + " | ".join(LATE) in sent(fake)


def test_background_questions_in_english_show_with_their_translation():
    orch, fake, out, *_ = make()
    orch.offer_questions((Question("What made you look now?", "מה גרם לכם לחפש עכשיו?"),))
    [shown] = events(out, "questions")
    assert shown["items"] == [{"text": "What made you look now?", "he": "מה גרם לכם לחפש עכשיו?"}]
    assert shown["lang"] == "en"


def test_background_questions_never_replace_shown_ones_and_never_show_while_ask_is_closed():
    opening, live, closed_ask, closed = make(questions=OPENING), make([ASK]), make(), make()
    live[0].on_line(line(1), "he")
    live[0].pump()
    closed_ask[0].set_sections(False, True)
    closed[0].close()
    for orch, fake, out, *_ in (opening, live, closed_ask, closed):
        before = events(out, "questions")
        orch.offer_questions(tuple(Question(q) for q in LATE))
        assert events(out, "questions") == before


def test_terms_carry_their_kind():
    reply = {"card": None, "goals": [], "terms": [{"term": "Dana", "note": "IT", "kind": "person"}]}
    orch, fake, out, *_ = make([reply])
    orch.on_line(line(1), "he")
    orch.pump()
    assert events(out, "term")[0]["kind"] == "person" and orch.term_list()[0]["kind"] == "person"


FILE = """# חברות

## Check Point
סוג: חברה
כתיבים: צ'ק פוינט, צק פוינט
תקציר: ענקית אבטחת רשת ישראלית
שלנו:
- שיחה עם ה-CISO, דנה, 2026-03-02, מקור: "סיכום" (https://example.com/a)
- כנס משותף, יוסי, 2026-05-10, מקור: "יומן" (https://example.com/b)

## Gem Security
סוג: חברה
כתיבים: גם*, Gem*
תקציר: זיהוי איומים בענן
שלנו: אין עדיין
"""


def kb():
    entries, skipped = parse(FILE, file="cyber")
    return Knowledge(entries, skipped=skipped)


def test_a_name_from_a_knowledge_file_shows_as_soon_as_the_line_comes():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה", channel="me"), "he")
    assert fake.calls == []
    [term] = events(out, "term")
    assert (term["term"], term["note"], term["kind"], term["lang"]) == ("Check Point", "ענקית אבטחת רשת ישראלית",
                                                                        "company", "he")
    assert term["known"] is True and term["trigger_line_ids"] == [1]
    assert term["ours"] == ["מאי 2026: כנס משותף, יוסי", "מרץ 2026: שיחה עם ה-CISO, דנה"]
    assert term["ours_links"] == ["https://example.com/b", "https://example.com/a"] and term["file"] == "cyber"
    shown = orch.term_list()[0]
    assert shown["known"] is True and shown["ours"][0].endswith("(https://example.com/b)")


def test_a_name_i_marked_as_known_never_shows_by_any_spelling():
    orch, fake, out, *_ = make(knowledge=kb(), ignored=("צ'ק פוינט",))
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה", channel="me"), "he")
    orch.on_line(line(2, "and Check Point too"), "en")
    assert events(out, "term") == []


def test_a_term_i_marked_as_known_is_not_shown_and_the_agent_is_told():
    reply = {"card": None, "goals": [], "terms": [{"term": "OKTA", "note": "התחברות", "kind": "product"},
                                                  {"term": "SCIM", "note": "סנכרון משתמשים", "kind": "term"}]}
    orch, fake, out, *_ = make([reply], ignored=("okta",))
    orch.on_line(line(1, "we use Okta with SCIM"), "en")
    orch.pump()
    assert [t["term"] for t in events(out, "term")] == ["SCIM"]
    assert "known_terms: okta" in fake.calls[0][1]


def test_an_everyday_word_waits_for_the_agent_and_then_shows_the_entry():
    reply = {"card": None, "goals": [], "terms": [{"term": "Gem", "note": "חברת ענן", "kind": "product"},
                                                  {"term": "Okta", "note": "התחברות לארגונים", "kind": "product"}]}
    orch, fake, out, *_ = make([reply], knowledge=kb())
    orch.on_line(line(1, "גם אנחנו עובדים עם גם"), "he")
    assert events(out, "term") == []
    orch.pump()
    terms = events(out, "term")
    assert [(t["term"], t["note"], t["kind"], t["known"]) for t in terms] == [
        ("Gem Security", "זיהוי איומים בענן", "company", True), ("Okta", "התחברות לארגונים", "product", False)]
    assert terms[0]["ours"] == [] and terms[1]["ours"] == []
    assert (terms[0]["file"], terms[0]["ours_links"]) == ("cyber", []) and (terms[1]["file"], terms[1]["ours_links"]) == ("", [])


def test_a_known_name_shows_once_and_the_agent_is_told():
    reply = {"card": None, "goals": [], "terms": [{"term": "Check Point", "note": "שוב", "kind": "company"},
                                                  {"term": "צ׳ק פוינט", "note": "שוב", "kind": "company"}]}
    orch, fake, out, *_ = make([reply, NONE], knowledge=kb())
    orch.on_line(line(1, "הם עובדים עם צק פוינט כבר שנים"), "he")
    orch.pump()
    orch.on_line(line(2, "וגם צ'ק פוינט שוב"), "he")
    orch.pump()
    assert [t["term"] for t in events(out, "term")] == ["Check Point"]
    assert "known_terms: Check Point" in sent(fake, 0)


def test_an_entry_the_agent_names_first_with_other_spacing_still_shows_as_known():
    file = FILE + "\n## Team8\nסוג: קרן\nכתיבים: טים 8\nתקציר: קרן ואולפן\nשלנו: פגישה, יוסי, 2026-01-20\n"
    entries, _ = parse(file)
    reply = {"card": None, "goals": [], "terms": [{"term": "Team 8", "note": "קרן", "kind": "company"}]}
    orch, fake, out, *_ = make([reply, NONE], knowledge=Knowledge(entries))
    orch.on_line(line(1, "השקיעו בנו תים אייט לפני שנה"), "he")
    orch.pump()
    orch.on_line(line(2, "וגם טים 8 עוד פעם"), "he")
    orch.pump()
    [term] = events(out, "term")
    assert (term["term"], term["known"], term["ours"]) == ("Team8", True, ["ינואר 2026: פגישה, יוסי"])


HEBREW_NAMED = ("## מנהל אבטחת מידע\nסוג: מונח\nכתיבים: CISO, Chief Information Security Officer, סיסו\n"
                "תקציר: מנהל האחראי לתוכנית האבטחה\n")


def test_a_term_said_in_english_shows_in_english_with_its_hebrew_note():
    reply = {"card": None, "goals": [], "terms": [{"term": "CISO", "note": "אחר", "kind": "term"}]}
    orch, fake, out, *_ = make([reply, NONE], knowledge=Knowledge(parse(HEBREW_NAMED)[0]))
    orch.on_line(line(1, "I talked to our Chief Information Security Officer"), "en")
    orch.pump()
    orch.on_line(line(2, "and the CISO agreed"), "en")
    orch.pump()
    [term] = events(out, "term")
    assert (term["term"], term["note"], term["known"]) == ("Chief Information Security Officer",
                                                           "מנהל האחראי לתוכנית האבטחה", True)


def test_a_term_the_agent_names_in_english_shows_in_english():
    reply = {"card": None, "goals": [], "terms": [{"term": "CISO", "note": "אחר", "kind": "term"}]}
    orch, fake, out, *_ = make([reply], knowledge=Knowledge(parse(HEBREW_NAMED)[0]))
    orch.on_line(line(1, "she runs security there"), "en")
    orch.pump()
    [term] = events(out, "term")
    assert (term["term"], term["note"]) == ("CISO", "מנהל האחראי לתוכנית האבטחה")


def test_a_term_said_in_hebrew_letters_shows_the_entry_name():
    orch, fake, out, *_ = make(knowledge=Knowledge(parse(HEBREW_NAMED)[0]))
    orch.on_line(line(1, "דיברתי עם הסיסו שלהם"), "he")
    [term] = events(out, "term")
    assert term["term"] == "מנהל אבטחת מידע"


def test_a_line_after_the_close_shows_no_name():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.close()
    orch.on_line(line(1, "צק פוינט"), "he")
    assert events(out, "term") == []


def test_without_knowledge_terms_are_the_agents():
    reply = {"card": None, "goals": [], "terms": [{"term": "Okta", "note": "התחברות", "kind": "product"}]}
    orch, fake, out, *_ = make([reply])
    orch.on_line(line(1, "אנחנו על צק פוינט ו-Okta"), "he")
    orch.pump()
    [term] = events(out, "term")
    assert term["term"] == "Okta" and term["known"] is False and term["ours"] == []


def test_a_name_in_a_sentence_still_being_said_shows_at_once_and_once():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.on_partial("והם עובדים עם צק פו")
    assert events(out, "term") == []
    orch.on_partial("והם עובדים עם צק פוינט כבר")
    orch.on_line(line(1, "והם עובדים עם צ'ק פוינט כבר שנים"), "he")
    [term] = events(out, "term")
    assert term["term"] == "Check Point" and term["known"] is True and term["trigger_line_ids"] == []


def test_a_partial_is_not_sent_to_the_agent_and_is_ignored_after_the_close():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.on_partial("אנחנו מדברים על הרבה דברים חשובים היום בשיחה הזאת")
    orch.pump()
    assert fake.calls == []
    orch.close()
    orch.on_partial("צק פוינט")
    assert events(out, "term") == []


OPENING = (Question("מה גרם לכם לחפש פתרון עכשיו?"),)


def test_the_opening_questions_show_until_live_ones_replace_them():
    orch, fake, out, *_ = make([ASK], questions=OPENING)
    orch.first_questions()
    orch.on_line(line(1), "he")
    orch.pump()
    assert [(texts(q), q["lang"], q["trigger_line_ids"]) for q in events(out, "questions")] == \
        [(["מה גרם לכם לחפש פתרון עכשיו?"], "he", []), (ASK["questions"], "he", [1])]
    assert "shown_questions: מה גרם לכם לחפש פתרון עכשיו?" in sent(fake)


def test_opening_questions_show_even_without_live_questions():
    orch, fake, out, *_ = make(call_type=LISTEN, questions=OPENING)
    orch.start()
    orch.close()
    assert [q["items"] for q in events(out, "questions")] == [[{"text": "מה גרם לכם לחפש פתרון עכשיו?", "he": ""}]]


def test_english_opening_questions_carry_their_translation():
    opening = (Question("What made you look now?", "מה גרם לכם לחפש עכשיו?"),)
    orch, fake, out, *_ = make(questions=opening)
    orch.first_questions()
    [shown] = events(out, "questions")
    assert shown["lang"] == "en" and shown["items"] == [{"text": "What made you look now?",
                                                         "he": "מה גרם לכם לחפש עכשיו?"}]


def test_a_coach_that_joins_after_ask_was_closed_still_gets_the_opening_questions():
    orch, fake, out, *_ = make(questions=OPENING, coach=False)
    orch.set_coach(True)
    orch.set_sections(False, True)
    orch.set_sections(True, True)
    orch.first_questions()
    assert [texts(q) for q in events(out, "questions")] == [["מה גרם לכם לחפש פתרון עכשיו?"]]


def test_without_an_agent_only_the_brief_opening_questions_show():
    bare, _, bare_out, *_ = make(provider=None)
    bare.first_questions()
    briefed, _, briefed_out, *_ = make(provider=None, questions=OPENING)
    briefed.first_questions()
    assert events(bare_out, "questions") == [] and len(events(briefed_out, "questions")) == 1


def test_goals_that_come_later_replace_the_first_ones():
    orch, fake, out, *_ = make()
    orch.set_goals([Goal("g1", "להבין את התקציב")])
    assert [g.text for g in orch.goal_list()] == ["להבין את התקציב"]
    assert events(out, "goals")[-1]["goals"] == [{"id": "g1", "text": "להבין את התקציב", "done": False}]
    orch.close()
    orch.set_goals([Goal("g1", "מאוחר מדי")])
    assert [g.text for g in orch.goal_list()] == ["להבין את התקציב"] and len(events(out, "goals")) == 1


def test_goals_that_come_later_keep_the_ones_I_already_work_with():
    focused, _, focused_out, *_ = make()
    first = [g.id for g in focused.goal_list()]
    focused.focus_goal(first[0])
    focused.set_goals([Goal("b1", "להבין את התקציב")])
    assert [g.id for g in focused.goal_list()] == first and focused.focus == first[0]
    by_hand, _, by_hand_out, *_ = make()
    by_hand.set_goal(by_hand.goal_list()[0].id, True)
    by_hand.set_goals([Goal("b1", "להבין את התקציב")])
    assert "b1" not in [g.id for g in by_hand.goal_list()]
    assert not any(g["goals"][0]["id"] == "b1" for g in events(focused_out, "goals") + events(by_hand_out, "goals"))


class GoalsWhileStepping(FakeProvider):
    """The background goals come while a step built under the first goals is still out."""

    def step(self, message):
        self.orch.set_goals([Goal("b1", "להבין את התקציב"), Goal("b2", "לקבוע המשך")])
        return super().step(message)


def test_a_reply_about_the_first_goals_marks_none_of_the_background_ones(tmp_path):
    stale = {"card": None, "goals": [{"id": "g1", "done": True}, {"id": "g2", "done": True}]}
    fake = GoalsWhileStepping([stale])
    orch, _, out, *_ = make(provider=fake, log=tmp_path / "events.log")
    fake.orch = orch
    orch.on_line(line(1, "אני זאת שמחליטה על זה"), "he")
    orch.pump()
    assert "- [g1] open: מי מחליט" in sent(fake)
    assert [(g.id, g.done) for g in orch.goal_list()] == [("b1", False), ("b2", False)]
    assert [n["id"] for n in notes(tmp_path / "events.log", "unknown_goal")] == ["g1", "g2"]
    assert len(events(out, "goals")) == 1


OKTA_TERM = {"card": None, "goals": [], "terms": [{"term": "Okta", "note": "שירות התחברות", "kind": "product"}]}
DETAIL = "שירות התחברות וניהול זהויות לארגונים. הוא עלה כי הצד השני סיפר שהם עובדים איתו."


def with_term(provider=None, **kw):
    """An orchestrator whose call has one term, Okta (id 1), said in a line now and in one 4 minutes ago."""
    clock = Clock()
    orch, fake, out, clock, used = make([OKTA_TERM], clock=clock, provider=provider or "fake", **kw)
    orch.on_line(line(1, "בשנה שעברה עברנו ספק בכלל", t=clock.t - 240), "he")
    orch.on_line(line(2, "אנחנו עובדים עם Okta כבר שנתיים", t=clock.t - 5), "he")
    orch.pump()
    return orch, orch.provider, out, used


def details(out):
    return [(e["id"], e["ok"], e["text"]) for e in events(out, "term_detail")]


def test_a_term_is_explained_by_the_live_model_from_the_last_three_minutes():
    fake = FakeProvider([OKTA_TERM], detail=DETAIL)
    orch, _, out, used = with_term(fake)
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, DETAIL)]
    [message] = [m for kind, m in fake.calls if kind == "explain"]
    assert "term: Okta" in message and "kind: product" in message and "note: שירות התחברות" in message
    assert "אנחנו עובדים עם Okta כבר שנתיים" in message and "עברנו ספק" not in message
    assert used == ["fake", "fake"] and orch.term_list()[0]["detail"] == DETAIL


def test_a_term_asked_again_answers_from_what_it_has():
    fake = FakeProvider([OKTA_TERM], detail=DETAIL)
    orch, _, out, _ = with_term(fake)
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    orch.explain_term(1)
    assert details(out) == [(1, True, DETAIL)] * 2
    assert [kind for kind, _ in fake.calls].count("explain") == 1


class Slow(FakeProvider):
    def __init__(self, **kw):
        super().__init__([OKTA_TERM], detail=DETAIL, **kw)
        self.gate = threading.Event()

    def explain(self, system, message):
        assert self.gate.wait(10)
        return super().explain(system, message)


def test_one_request_per_term_and_the_live_step_never_waits():
    slow = Slow()
    orch, _, out, _ = with_term(slow)
    orch.explain_term(1)
    orch.explain_term(1)
    orch.on_line(line(3, "ומי מאשר את הבקשות האלה אצלכם"), "he")
    assert orch.pump() is True and details(out) == []
    slow.gate.set()
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, DETAIL)] and [kind for kind, _ in slow.calls].count("explain") == 1


def test_a_term_asked_after_the_call_ended_answers_not_ok_at_once():
    fake = FakeProvider([OKTA_TERM], detail=DETAIL)
    orch, _, out, _ = with_term(fake)
    orch.close()
    orch.explain_term(1)
    assert details(out) == [(1, False, "השיחה כבר הסתיימה")]
    assert [kind for kind, _ in fake.calls].count("explain") == 0


def test_an_unknown_term_answers_not_ok_at_once():
    orch, fake, out, *_ = make()
    orch.explain_term(7)
    [(term_id, ok, text)] = details(out)
    assert (term_id, ok) == (7, False) and text and fake.calls == []


def test_a_failed_explanation_says_so_and_can_be_asked_again(tmp_path):
    fake = FakeProvider([OKTA_TERM], detail=DETAIL, errors=[None, ProviderUnavailable("busy")])
    orch, _, out, _ = with_term(fake, log=tmp_path / "events.log")
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, False, "לא הצלחתי להרחיב")] and "detail" not in orch.term_list()[0]
    assert notes(tmp_path / "events.log", "detail_failed")[0]["message"] == "busy"
    orch.explain_term(1)
    assert wait_for(lambda: len(details(out)) == 2)
    assert details(out)[-1] == (1, True, DETAIL)


def test_an_empty_explanation_is_a_failure():
    fake = FakeProvider([OKTA_TERM], detail="  ")
    orch, _, out, _ = with_term(fake)
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, False, "לא הצלחתי להרחיב")]


def test_a_long_explanation_is_cut():
    fake = FakeProvider([OKTA_TERM], detail=" ".join(["מילה"] * 90))
    orch, _, out, _ = with_term(fake)
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    text = details(out)[0][2]
    assert len(text.split()) == 60 and text.endswith("…")


def test_without_an_agent_a_term_is_not_explained():
    orch, fake, out, *_ = make(provider=None, knowledge=kb())
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה"), "he")
    orch.explain_term(1)
    assert details(out) == [(1, False, "לא הצלחתי להרחיב")]


DETAILED = """## Check Point
סוג: חברה
כתיבים: צ'ק פוינט
תקציר: ענקית אבטחת רשת ישראלית
פרטים:
- תחום: אבטחת רשת
- הוקמה: 1993, מטה: תל אביב
שלנו: אין עדיין
"""
FILE_DETAIL = "תחום: אבטחת רשת\nהוקמה: 1993, מטה: תל אביב"
MORE = "היא עלתה כי הצד השני משתמש בחומת האש שלה."


class Rules(FakeProvider):
    """A fake that keeps the rules of each explanation it was asked for."""

    def __init__(self, **kw):
        super().__init__([NONE] * 10, detail=MORE, **kw)
        self.rules = []

    def explain(self, system, message):
        self.rules.append(system)
        return super().explain(system, message)


def with_known(text=DETAILED, coach=True):
    """An orchestrator whose call has one known name with details, Check Point (id 1), said a moment ago."""
    fake = Rules()
    orch, _, out, *_ = make(provider=fake, knowledge=Knowledge(parse(text, file="cyber")[0]), coach=coach)
    orch.set_sections(False, True)
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה"), "he")
    return orch, fake, out


def test_a_known_name_answers_with_its_details_from_the_file_with_no_request():
    orch, fake, out = with_known()
    assert events(out, "term")[0]["detail"] is True
    orch.explain_term(1)
    assert details(out) == [(1, True, FILE_DETAIL)] and fake.rules == []
    assert orch.term_list()[0]["detail"] == FILE_DETAIL


def test_a_name_without_details_says_so_in_its_event():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה"), "he")
    assert events(out, "term")[0]["detail"] is False and "detail" not in orch.term_list()[0]


def test_more_from_the_model_is_added_under_the_files_details():
    orch, fake, out = with_known()
    orch.explain_term(1, more=True)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, f"{FILE_DETAIL}\n\n{MORE}")]
    [message] = [m for kind, m in fake.calls if kind == "explain"]
    assert "## What my knowledge file already says\n" + FILE_DETAIL in message and "term: Check Point" in message
    assert "more from the model" in fake.rules[0]
    orch.explain_term(1, more=True)
    orch.explain_term(1)
    assert details(out)[1:] == [(1, True, f"{FILE_DETAIL}\n\n{MORE}")] * 2 and len(fake.rules) == 1


def test_more_that_fails_keeps_the_files_details():
    orch, fake, out = with_known()
    fake.errors = [ProviderUnavailable("busy")]
    orch.explain_term(1, more=True)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, False, "לא הצלחתי להרחיב")] and orch.term_list()[0]["detail"] == FILE_DETAIL
    orch.explain_term(1)
    assert details(out)[-1] == (1, True, FILE_DETAIL)


def test_more_with_the_coach_off_asks_nothing_but_the_details_still_show():
    orch, fake, out = with_known(coach=False)
    orch.explain_term(1, more=True)
    orch.explain_term(1)
    assert details(out) == [(1, False, "המאמן כבוי"), (1, True, FILE_DETAIL)] and fake.rules == []


def test_more_on_a_term_with_no_details_is_a_plain_click():
    fake = FakeProvider([OKTA_TERM], detail=DETAIL)
    orch, _, out, _ = with_term(fake)
    orch.explain_term(1, more=True)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, DETAIL)]
    [message] = [m for kind, m in fake.calls if kind == "explain"]
    assert "knowledge file" not in message


def test_more_on_a_local_only_file_never_sends_its_details():
    orch, fake, out = with_known("למודל: לא\n\n" + DETAILED)
    orch.explain_term(1, more=True)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, f"{FILE_DETAIL}\n\n{MORE}")]
    [message] = [m for kind, m in fake.calls if kind == "explain"]
    assert "אבטחת רשת\n" not in message and "1993" not in message and "knowledge file" not in message
    assert "more from the model" not in fake.rules[0]


FULL = {"card": {"kind": "question", "text": "Who signs off on it?", "lang": "en", "he": "מי מאשר את זה?"},
        "goals": [{"id": "g1", "done": True}], "terms": [{"term": "SOC 2", "note": "תקן אבטחה", "kind": "term"}],
        "questions": [{"text": "What did the audit cost?", "he": "כמה עלתה הביקורת?"}],
        "asked": {"text": "How is yours different?", "he": "במה שלכם שונה?"}}
AUDIT = "we spent a month on the audit"


def say(orch, i, text=AUDIT, channel="them"):
    orch.on_line(line(i, text, channel=channel, lang="en"), "en")
    return orch.pump()


def test_a_full_reply_fills_every_open_section():
    orch, fake, out, *_ = make([FULL])
    say(orch, 1)
    [card] = events(out, "card")
    assert (card["kind"], card["text"], card["lang"], card["he"]) == ("question", "Who signs off on it?", "en",
                                                                      "מי מאשר את זה?")
    assert [q["items"] for q in events(out, "questions")] == [[{"text": "What did the audit cost?",
                                                                "he": "כמה עלתה הביקורת?"}]]
    assert [(a["text"], a["he"], a["trigger_line_ids"]) for a in events(out, "asked")] == \
        [("How is yours different?", "במה שלכם שונה?", [1])]
    assert [t["term"] for t in events(out, "term")] == ["SOC 2"]
    message = sent(fake)
    for part in ("sections: ask on, terms on", "hearing_me: on", "shown_asked: (none)"):
        assert part in message


def test_a_closed_ask_section_shows_no_card_questions_or_asked_and_goals_still_update():
    orch, fake, out, *_ = make([FULL])
    orch.set_sections(False, True)
    say(orch, 1)
    assert "sections: ask off, terms on" in sent(fake)
    assert events(out, "card") == [] and events(out, "questions") == [] and events(out, "asked") == []
    assert [g["done"] for g in events(out, "goals")[-1]["goals"]] == [True, False]
    assert events(out, "term")


def test_closing_ask_forgets_the_questions_and_the_asked_question():
    orch, fake, out, *_ = make([FULL, dict(FULL)])
    say(orch, 1)
    orch.set_sections(False, True)
    orch.set_sections(True, True)
    say(orch, 2, "and then we did it all again")
    message = sent(fake, 1)
    assert "shown_questions: (none)" in message and "shown_asked: (none)" in message
    assert len(events(out, "questions")) == 2 and len(events(out, "asked")) == 2


def test_while_ask_is_closed_a_forced_question_and_the_meter_tip_wait(tmp_path):
    clock = Clock()
    orch, fake, out, *_ = make(clock=clock, meter=Meter(), log=tmp_path / "events.log")
    orch.set_sections(False, True)
    orch.ask_now(clock.t)
    t = clock.t
    for i in range(20):
        orch.on_segment(seg("me", 9.0, t + i * 10 + 9))
        orch.on_segment(seg("them", 1.0, t + i * 10 + 10))
    clock.t = t + 200
    assert orch.pump() is False
    assert events(out, "card") == [] and fake.calls == []
    assert notes(tmp_path / "events.log", "ask_now_closed")


def test_a_closed_terms_section_finds_no_term_and_keeps_the_ones_shown():
    orch, fake, out, *_ = make([FULL, dict(FULL)], knowledge=kb())
    orch.on_line(line(1, "כן, ולצ'ק פוינט יש את זה"), "he")
    orch.pump()
    shown = [t["term"] for t in events(out, "term")]
    orch.set_sections(True, False)
    assert orch.wants_partials() is False
    orch.on_partial("ושוב צ'ק פוינט")
    say(orch, 2)
    assert "sections: ask on, terms off" in sent(fake, 1)
    assert [t["term"] for t in events(out, "term")] == shown and [t["term"] for t in orch.term_list()] == shown


def test_with_every_section_closed_no_request_is_sent_and_the_lines_are_dropped():
    orch, fake, out, *_ = make([NONE])
    orch.set_sections(False, False)
    orch.on_line(line(1), "he")
    assert orch.pump() is False and fake.calls == []
    orch.set_sections(True, True)
    orch.on_line(line(2, "ומה עם התקציב לשנה הבאה"), "he")
    assert orch.pump() is True
    assert "ומה עם התקציב לשנה הבאה" in sent(fake) and LONG not in sent(fake)


def test_with_every_section_closed_a_term_is_still_explained():
    fake = FakeProvider([OKTA_TERM], detail=DETAIL)
    orch, _, out, _ = with_term(fake)
    orch.set_sections(False, False)
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, DETAIL)]


def test_the_sections_note_records_each_change(tmp_path):
    orch, *_ = make(log=tmp_path / "events.log")
    orch.set_sections(False, True)
    [note] = notes(tmp_path / "events.log", "sections")
    assert (note["ask"], note["terms"]) == (False, True)


def asked_reply(text=None, he=""):
    return {"card": None, "goals": [], "asked": {"text": text, "he": he} if text else None}


def asked_events(out):
    return [(a["text"], a["he"]) for a in events(out, "asked")]


def test_the_asked_question_shows_once_changes_and_clears():
    replies = [asked_reply("How is yours different?", "במה שלכם שונה?"),
               asked_reply("How is yours different?", "במה שלכם שונה?"),
               asked_reply("What does it cost?", "כמה זה עולה?"), asked_reply(), asked_reply()]
    orch, fake, out, *_ = make(replies)
    for i in range(1, 6):
        say(orch, i, f"line number {i} of the call")
    assert asked_events(out) == [("How is yours different?", "במה שלכם שונה?"), ("What does it cost?", "כמה זה עולה?"),
                                 ("", "")]
    assert "shown_asked: How is yours different?" in sent(fake, 1)


def test_while_a_question_asked_of_me_shows_my_own_line_asks_the_model():
    orch, fake, out, *_ = make([asked_reply("How is yours different?"), asked_reply(), NONE])
    assert say(orch, 1, "my line before any question", channel="me") is False
    assert say(orch, 2) is True
    assert say(orch, 3, "ours runs on premises", channel="me") is True
    assert asked_events(out)[-1] == ("", "")
    assert say(orch, 4, "and it is fast too", channel="me") is False


def test_a_short_line_of_mine_does_not_ask_even_while_a_question_shows():
    orch, fake, out, *_ = make([asked_reply("How is yours different?")])
    say(orch, 1)
    assert say(orch, 2, "yes sure", channel="me") is False


def test_hearing_off_clears_the_asked_question_and_ignores_new_ones():
    orch, fake, out, *_ = make([asked_reply("How is yours different?"), asked_reply("What does it cost?"),
                                asked_reply("What does it cost?")])
    say(orch, 1)
    orch.set_hearing(False)
    assert asked_events(out) == [("How is yours different?", ""), ("", "")]
    say(orch, 2)
    assert "hearing_me: off" in sent(fake, 1) and "shown_asked: (none)" in sent(fake, 1)
    assert len(asked_events(out)) == 2
    orch.set_hearing(True)
    say(orch, 3)
    assert "hearing_me: on" in sent(fake, 2) and asked_events(out)[-1] == ("What does it cost?", "")


def test_hearing_off_with_no_asked_question_sends_nothing():
    orch, fake, out, *_ = make()
    orch.set_hearing(False)
    orch.set_hearing(False)
    orch.set_hearing(True)
    assert events(out, "asked") == []


def test_a_closed_ask_section_drops_the_asked_question_from_the_reply():
    orch, fake, out, *_ = make([asked_reply("How is yours different?")])
    orch.set_sections(False, True)
    say(orch, 1)
    assert events(out, "asked") == []


BRIEF_TERMS = Knowledge([Entry("SOC 2", "term", "תקן ביקורת אבטחה לספקי שירותים"),
                         Entry("Nimbus Labs", "term", "המתחרה העיקרי של הלקוח")])


def test_a_brief_term_in_a_line_shows_at_once_without_a_request():
    orch, fake, out, *_ = make(brief_terms=BRIEF_TERMS)
    orch.on_line(line(1, "SOC 2", lang="en"), "en")
    assert orch.pump() is False and fake.calls == []
    [term] = events(out, "term")
    assert (term["term"], term["note"], term["kind"], term["lang"], term["known"], term["ours"],
            term["trigger_line_ids"]) == ("SOC 2", "תקן ביקורת אבטחה לספקי שירותים", "term", "he", False, [], [1])


def test_a_brief_term_in_a_partial_shows_once():
    orch, fake, out, *_ = make(brief_terms=BRIEF_TERMS)
    assert orch.wants_partials() is True
    orch.on_partial("we looked at nimbus labs last year")
    orch.on_line(line(1, "we looked at Nimbus Labs last year", lang="en"), "en")
    assert [(t["term"], t["trigger_line_ids"]) for t in events(out, "term")] == [("Nimbus Labs", [])]


def test_a_brief_term_the_model_names_keeps_the_brief_note():
    reply = {"card": None, "goals": [], "terms": [{"term": "soc 2", "note": "משהו אחר", "kind": "product"}]}
    orch, fake, out, *_ = make([reply], brief_terms=BRIEF_TERMS)
    say(orch, 1, "we need the sock two report")
    [term] = events(out, "term")
    assert (term["term"], term["note"], term["kind"]) == ("SOC 2", "תקן ביקורת אבטחה לספקי שירותים", "term")


def test_a_knowledge_entry_wins_over_a_brief_term_of_the_same_name():
    knowledge = Knowledge([Entry("SOC 2", "term", "מה שכתוב בקובץ הידע")])
    orch, fake, out, *_ = make(knowledge=knowledge, brief_terms=BRIEF_TERMS)
    orch.on_partial("SOC 2")
    assert [(t["term"], t["note"]) for t in events(out, "term")] == [("SOC 2", "מה שכתוב בקובץ הידע")]


def test_partials_run_only_with_a_name_to_find():
    assert make()[0].wants_partials() is False
    assert make(knowledge=kb())[0].wants_partials() is True


def test_a_brief_term_is_explained_by_the_model_like_any_term():
    fake = FakeProvider([], detail=DETAIL)
    orch, _, out, *_ = make(provider=fake, brief_terms=BRIEF_TERMS)
    orch.on_partial("SOC 2")
    orch.explain_term(1)
    assert wait_for(lambda: details(out))
    assert details(out) == [(1, True, DETAIL)]


SPOTTED = json.dumps({"terms": [{"term": "SCIM", "note": "תקן לסנכרון משתמשים בין מערכות", "kind": "term"},
                                {"term": "Okta", "note": "שירות התחברות", "kind": "product"}]})
NOTHING = '{"terms": []}'


def spotted(out):
    return [(e["ok"], e["ids"]) for e in events(out, "terms_now")]


def spot_calls(fake):
    return [m for kind, m in fake.calls if kind == "spot"]


def test_terms_now_finds_the_terms_of_the_last_30_seconds_newest_first():
    fake = FakeProvider([OKTA_TERM], spotted=SPOTTED)
    orch, _, out, used = with_term(fake)
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    assert spotted(out) == [(True, [2, 1])]
    new = events(out, "term")[-1]
    assert (new["id"], new["term"], new["known"], new["trigger_line_ids"]) == (2, "SCIM", False, [])
    [message] = spot_calls(fake)
    assert "אנחנו עובדים עם Okta כבר שנתיים" in message and "עברנו ספק" not in message
    assert "## Shown terms\nOkta\n" in message and used == ["fake", "fake"]


class SlowSpot(FakeProvider):
    def __init__(self, **kw):
        super().__init__([OKTA_TERM], spotted=SPOTTED, **kw)
        self.gate = threading.Event()

    def spot(self, system, message):
        assert self.gate.wait(10)
        return super().spot(system, message)


def test_terms_now_runs_beside_the_live_step_one_search_at_a_time():
    slow = SlowSpot()
    orch, _, out, _ = with_term(slow)
    orch.terms_now()
    orch.terms_now()
    orch.on_line(line(3, "ומי מאשר את הבקשות האלה אצלכם"), "he")
    assert orch.pump() is True and spotted(out) == []
    assert "ומי מאשר את הבקשות האלה אצלכם" in sent(slow, 1) and "SCIM" not in sent(slow, 1)
    slow.gate.set()
    assert wait_for(lambda: spotted(out))
    assert spotted(out) == [(True, [2, 1])] and len(spot_calls(slow)) == 1
    orch.terms_now()
    assert wait_for(lambda: len(spotted(out)) == 2) and len(spot_calls(slow)) == 2


def test_terms_now_reads_the_sentence_still_being_said():
    fake = FakeProvider([OKTA_TERM], spotted=NOTHING)
    orch, _, out, _ = with_term(fake)
    orch.on_partial("ואז חיברנו את זה ל-SCIM")
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    assert spotted(out) == [(True, [])]
    assert "[still speaking] them: ואז חיברנו את זה ל-SCIM" in spot_calls(fake)[0]


def test_a_partial_is_left_out_once_its_line_came_or_it_is_old():
    fake = FakeProvider([NONE] * 5, spotted=NOTHING)
    orch, _, out, clock, _ = make(provider=fake)
    orch.on_partial("ואז חיברנו")
    orch.on_line(line(1, "ואז חיברנו את זה ל-SCIM", t=clock.t - 2), "he")
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    orch.on_partial("ואז עברנו")
    clock.t += 9
    orch.terms_now()
    assert wait_for(lambda: len(spotted(out)) == 2)
    assert [("still speaking" in m) for m in spot_calls(fake)] == [False, False]


def test_terms_now_with_nothing_said_lately_answers_at_once_without_a_request():
    orch, fake, out, clock, _ = make()
    orch.on_line(line(1, t=clock.t - 60), "he")
    orch.terms_now()
    assert spotted(out) == [(True, [])] and spot_calls(fake) == []


def test_terms_now_without_an_agent_after_the_call_or_with_terms_closed_is_not_ok_at_once():
    orch, _, out, *_ = make(provider=None)
    orch.on_line(line(1), "he")
    orch.terms_now()
    assert spotted(out) == [(False, [])]
    orch, fake, out, *_ = make()
    orch.on_line(line(1), "he")
    orch.set_sections(True, False)
    orch.terms_now()
    orch.close()
    orch.terms_now()
    assert spotted(out) == [(False, [])] * 2 and spot_calls(fake) == []


def test_a_term_found_now_that_is_in_a_knowledge_file_shows_its_entry():
    found = json.dumps({"terms": [{"term": "Check Point", "note": "חברה", "kind": "company"}]})
    fake = FakeProvider([NONE], spotted=found)
    orch, _, out, clock, _ = make(provider=fake, knowledge=kb())
    orch.on_line(line(1, "הספק של חומות האש שלנו", t=clock.t - 3), "he")
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    [term] = events(out, "term")
    assert spotted(out) == [(True, [term["id"]])] and term["known"] is True
    assert term["note"] == "ענקית אבטחת רשת ישראלית"


def test_a_failed_or_bad_search_is_not_ok_and_can_be_asked_again(tmp_path):
    fake = FakeProvider([OKTA_TERM], spotted="not json", errors=[None, ProviderUnavailable("busy")])
    orch, _, out, _ = with_term(fake, log=tmp_path / "events.log")
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    assert notes(tmp_path / "events.log", "terms_now_failed")[0]["message"] == "busy"
    orch.terms_now()
    assert wait_for(lambda: len(spotted(out)) == 2)
    assert spotted(out) == [(False, [])] * 2


CHECKED = json.dumps({"terms": [{"term": "Okta Workflows", "note": "אוטומציה של תהליכי זהות", "kind": "product"}]})


def picked_answers(out):
    return [(e["ok"], e["ids"]) for e in events(out, "terms_now") if e["picked"]]


def test_picked_words_go_to_the_live_model_with_the_last_three_minutes():
    fake = FakeProvider([OKTA_TERM], spotted=CHECKED)
    orch, _, out, used = with_term(fake)
    orch.check_terms(["אוקטה  וורקפלואוס"])
    assert wait_for(lambda: picked_answers(out))
    assert picked_answers(out) == [(True, [2])]
    assert (events(out, "term")[-1]["term"], events(out, "term")[-1]["known"]) == ("Okta Workflows", False)
    [message] = spot_calls(fake)
    assert "## Picked\n- אוקטה וורקפלואוס\n" in message and "אנחנו עובדים עם Okta כבר שנתיים" in message
    assert "עברנו ספק" not in message and used == ["fake", "fake"]


def test_a_picked_name_from_a_knowledge_file_shows_at_once_with_no_request():
    orch, fake, out, *_ = make(knowledge=kb())
    orch.check_terms(["לצ'ק פוינט"])
    [term] = events(out, "term")
    assert picked_answers(out) == [(True, [term["id"]])] and term["known"] is True and spot_calls(fake) == []


def test_picked_words_known_and_new_answer_together_in_the_order_picked():
    fake = FakeProvider([NONE], spotted=CHECKED)
    orch, _, out, *_ = make(provider=fake, knowledge=kb())
    orch.check_terms(["צ'ק פוינט", "וורקפלואוס"])
    assert wait_for(lambda: picked_answers(out))
    known, new = events(out, "term")
    assert picked_answers(out) == [(True, [known["id"], new["id"]])] and "- וורקפלואוס" in spot_calls(fake)[0]
    assert "צ'ק פוינט" not in spot_calls(fake)[0].split("## Transcript")[0]


def test_a_check_runs_beside_a_search_and_answers_picked():
    slow = SlowSpot()
    orch, _, out, _ = with_term(slow)
    orch.terms_now()
    orch.check_terms(["סקים"])
    slow.gate.set()
    assert wait_for(lambda: len(events(out, "terms_now")) == 2)
    assert sorted(e["picked"] for e in events(out, "terms_now")) == [False, True] and len(spot_calls(slow)) == 2


def test_a_check_without_an_agent_or_with_terms_closed_is_not_ok_at_once():
    orch, _, out, *_ = make(provider=None)
    orch.check_terms(["סקים"])
    orch2, fake, out2, *_ = make()
    orch2.set_sections(True, False)
    orch2.check_terms(["סקים"])
    assert picked_answers(out) == [(False, [])] and picked_answers(out2) == [(False, [])] and spot_calls(fake) == []


SCREEN = json.dumps({"screen": "שקף שמשווה שלוש חבילות של Okta", "terms": [
    {"term": "Okta Identity Governance", "note": "ניהול הרשאות וביקורות גישה", "kind": "product"}]})


def screens(out):
    return [(e["ok"], e["text"]) for e in events(out, "screen")]


def test_a_screenshot_is_read_by_the_live_model_and_becomes_a_note(tmp_path):
    shot = tmp_path / "150312.jpg"
    shot.write_bytes(b"\xff\xd8 jpeg bytes")
    fake = FakeProvider([OKTA_TERM], screen=SCREEN)
    orch, _, out, used = with_term(fake, title="שיחה עם Acme")
    orch.add_screen(str(shot), "זה הדמו שלהם")
    assert wait_for(lambda: screens(out))
    assert screens(out) == [(True, "שקף שמשווה שלוש חבילות של Okta")]
    assert fake.images == [(b"\xff\xd8 jpeg bytes", "image/jpeg")]
    assert orch.context_notes() == ["זה הדמו שלהם", "מהמסך: שקף שמשווה שלוש חבילות של Okta"]
    assert events(out, "term")[-1]["term"] == "Okta Identity Governance"
    [message] = [m for kind, m in fake.calls if kind == "look"]
    assert "title: שיחה עם Acme" in message and "זה הדמו שלהם" in message and "אנחנו עובדים עם Okta" in message
    assert used == ["fake", "fake"]
    orch.on_line(line(3, "ומי מאשר את הבקשות האלה אצלכם"), "he")
    orch.pump()
    assert "- מהמסך: שקף שמשווה שלוש חבילות של Okta" in sent(fake, 1)


def test_a_screenshot_that_cannot_be_read_says_so(tmp_path):
    fake = FakeProvider([NONE], screen="not json")
    orch, _, out, *_ = make(provider=fake, log=tmp_path / "events.log")
    orch.add_screen(str(tmp_path / "missing.png"), "")
    assert wait_for(lambda: screens(out))
    shot = tmp_path / "a.png"
    shot.write_bytes(b"png")
    orch.add_screen(str(shot), "")
    assert wait_for(lambda: len(screens(out)) == 2)
    assert screens(out) == [(False, "לא הצלחתי לקרוא את המסך")] * 2 and orch.context_notes() == []
    assert notes(tmp_path / "events.log", "screen_failed")[0]["error"] == "FileNotFoundError"


def test_a_screenshot_without_an_agent_keeps_its_note_and_answers_not_ok():
    orch, _, out, *_ = make(provider=None)
    orch.add_screen("/x/a.png", "הדמו")
    assert screens(out) == [(False, "לא הצלחתי לקרוא את המסך")] and orch.context_notes() == ["הדמו"]


RECAP = "דנה סיפרה שהביקורת לקחה חודש\nהם עובדים עם Okta\nסיכמנו לשלוח הצעה עד יום חמישי"


def recaps(out):
    return [(e["ok"], e["text"]) for e in events(out, "recap")]


def recap_calls(fake):
    return [m for kind, m in fake.calls if kind == "recap"]


def test_a_recap_comes_from_the_strong_model_with_the_title_notes_and_last_half_hour():
    fake = FakeProvider([NONE, NONE], recap="- " + RECAP.replace("\n", "\n- "))
    clock = Clock()
    orch, _, out, _, used = make(provider=fake, clock=clock, title="שיחה עם דנה", call_notes="היא מנהלת IT")
    orch.add_context("לשאול על התקציב")
    orch.on_line(line(1, "this was said long ago", t=clock.t - 2000, lang="en"), "en")
    orch.on_line(line(2, AUDIT, t=clock.t - 60, lang="en"), "en")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    assert recaps(out) == [(True, RECAP)]
    [message] = recap_calls(fake)
    for part in ("title: שיחה עם דנה", "- היא מנהלת IT", "- לשאול על התקציב", AUDIT):
        assert part in message
    assert "this was said long ago" not in message and used == ["fake"]


def test_a_recap_knows_when_callcoach_did_not_hear_me():
    fake = FakeProvider([NONE, NONE], recap=RECAP)
    clock = Clock()
    orch, _, out, *_ = make(provider=fake, clock=clock)
    orch.on_line(line(1, AUDIT, t=clock.t - 60, lang="en"), "en")
    clock.t += 30
    orch.set_hearing(False)
    clock.t += 60
    orch.set_hearing(True)
    clock.t += 10
    orch.set_hearing(False)
    orch.recap()
    assert wait_for(lambda: recaps(out))
    [message] = recap_calls(fake)
    assert "## Times CallCoach did not hear me" in message
    assert message.count(" to ") >= 2 and " to now" in message


def test_a_recap_asked_again_with_no_new_line_answers_at_once():
    fake = FakeProvider([NONE], recap=RECAP)
    orch, _, out, *_ = make(provider=fake)
    orch.on_line(line(1), "he")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    orch.recap()
    assert recaps(out) == [(True, RECAP)] * 2 and len(recap_calls(fake)) == 1
    orch.on_line(line(2), "he")
    orch.recap()
    assert wait_for(lambda: len(recaps(out)) == 3)
    assert len(recap_calls(fake)) == 2


class SlowRecap(FakeProvider):
    def __init__(self):
        super().__init__([NONE, NONE], recap=RECAP)
        self.gate = threading.Event()

    def recap(self, system, message):
        assert self.gate.wait(10)
        return super().recap(system, message)


def test_one_recap_at_a_time_and_the_live_step_never_waits():
    slow = SlowRecap()
    orch, _, out, *_ = make(provider=slow)
    orch.recap()
    orch.recap()
    orch.on_line(line(1), "he")
    assert orch.pump() is True and recaps(out) == []
    slow.gate.set()
    assert wait_for(lambda: recaps(out))
    assert recaps(out) == [(True, RECAP)] and len(recap_calls(slow)) == 1


def test_a_recap_after_the_call_ended_answers_not_ok():
    orch, fake, out, *_ = make()
    orch.close()
    orch.recap()
    assert recaps(out) == [(False, "השיחה כבר הסתיימה")] and recap_calls(fake) == []


def test_a_failed_or_empty_recap_says_so_and_can_be_asked_again(tmp_path):
    fake = FakeProvider([], recap=RECAP, errors=[ProviderUnavailable("busy")])
    orch, _, out, *_ = make(provider=fake, log=tmp_path / "events.log")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    assert recaps(out) == [(False, "לא הצלחתי לסכם")]
    assert notes(tmp_path / "events.log", "recap_failed")[0]["message"] == "busy"
    orch.recap()
    assert wait_for(lambda: len(recaps(out)) == 2)
    assert recaps(out)[-1] == (True, RECAP)
    empty = FakeProvider([], recap="  ")
    orch, _, out, *_ = make(provider=empty)
    orch.recap()
    assert wait_for(lambda: recaps(out))
    assert recaps(out) == [(False, "לא הצלחתי לסכם")]


def test_a_long_recap_is_cut_to_five_lines():
    fake = FakeProvider([], recap="\n".join(f"{i}. שורה מספר {i}" for i in range(1, 8)))
    orch, _, out, *_ = make(provider=fake)
    orch.recap()
    assert wait_for(lambda: recaps(out))
    [(ok, text)] = recaps(out)
    assert ok and text.split("\n") == [f"שורה מספר {i}" for i in range(1, 6)]


def updates(out):
    return [e for e in events(out, "recap") if "since" in e]


def test_an_update_reads_only_what_is_new_and_says_since_when():
    fake = FakeProvider([NONE] * 4, recap=RECAP)
    clock = Clock()
    orch, _, out, *_ = make(provider=fake, clock=clock)
    orch.on_line(line(1, "this was said long before", t=clock.t - 300, lang="en"), "en")
    orch.on_line(line(2, AUDIT, t=clock.t - 60, lang="en"), "en")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    fake.recap_reply = "- מארק ביקש הצעת מחיר עד מחר"
    clock.t += 30
    orch.on_line(line(3, "Mark wants a quote by tomorrow", t=clock.t - 5, lang="en"), "en")
    orch.recap(since=True)
    assert wait_for(lambda: updates(out))
    [update] = updates(out)
    assert update["ok"] and update["text"] == "מארק ביקש הצעת מחיר עד מחר" and update["since"]
    first, second = recap_calls(fake)
    assert "## What I was already told" not in first
    assert "## What I was already told" in second and "- הם עובדים עם Okta" in second
    assert "Mark wants a quote by tomorrow" in second and "this was said long before" not in second


def test_an_update_with_no_new_line_says_nothing_new_at_once():
    fake = FakeProvider([NONE] * 2, recap=RECAP)
    orch, _, out, *_ = make(provider=fake)
    orch.on_line(line(1), "he")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    orch.recap(since=True)
    [update] = updates(out)
    assert update["ok"] and update["text"] == "" and len(recap_calls(fake)) == 1


def test_an_update_the_model_finds_nothing_new_in_is_empty_and_keeps_the_answers():
    fake = FakeProvider([NONE] * 4, recap=RECAP)
    clock = Clock()
    orch, _, out, *_ = make(provider=fake, clock=clock)
    orch.on_line(line(1, t=clock.t - 30), "he")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    fake.recap_reply = "אין חדש."
    orch.on_line(line(2, "כן", t=clock.t - 2), "he")
    orch.recap(since=True)
    assert wait_for(lambda: updates(out))
    assert [(u["ok"], u["text"]) for u in updates(out)] == [(True, "")]
    fake.recap_reply = "ההצעה תצא ביום חמישי"
    orch.on_line(line(3, t=clock.t), "he")
    orch.recap(since=True)
    assert wait_for(lambda: len(updates(out)) == 2)
    assert "אין חדש" not in recap_calls(fake)[-1]


def test_an_update_before_any_recap_is_the_recap():
    fake = FakeProvider([NONE], recap=RECAP)
    orch, _, out, *_ = make(provider=fake)
    orch.on_line(line(1), "he")
    orch.recap(since=True)
    assert wait_for(lambda: recaps(out))
    assert recaps(out) == [(True, RECAP)] and updates(out) == []


def test_a_failed_update_says_so_and_is_not_kept():
    fake = FakeProvider([NONE] * 4, recap=RECAP)
    clock = Clock()
    orch, _, out, *_ = make(provider=fake, clock=clock)
    orch.on_line(line(1, t=clock.t - 30), "he")
    orch.recap()
    assert wait_for(lambda: recaps(out))
    fake.recap_reply = "  "
    orch.on_line(line(2, t=clock.t - 2), "he")
    orch.recap(since=True)
    assert wait_for(lambda: updates(out))
    assert [(u["ok"], u["text"]) for u in updates(out)] == [(False, "לא הצלחתי לסכם")]
    fake.recap_reply = "ההצעה תצא ביום חמישי"
    orch.recap(since=True)
    assert wait_for(lambda: len(updates(out)) == 2)
    assert updates(out)[-1]["text"] == "ההצעה תצא ביום חמישי"


def test_without_an_agent_there_is_no_recap():
    orch, fake, out, *_ = make(provider=None)
    orch.recap()
    assert recaps(out) == [(False, "לא הצלחתי לסכם")]


def test_in_a_hebrew_call_english_live_questions_and_cards_show_their_hebrew():
    reply = {"card": {"kind": "question", "text": "How did you split equity?", "lang": "en",
                      "he": "איך חילקתם את המניות?"},
             "goals": [], "terms": [],
             "questions": [{"text": "Who raised the first round?", "he": "מי גייס את הסבב הראשון?"},
                           {"text": "Why now?", "he": ""}]}
    orch, fake, out, *_ = make([reply, dict(reply)])
    orch.on_line(line(1), "he")
    orch.pump()
    [shown] = events(out, "questions")
    assert shown["items"] == [{"text": "מי גייס את הסבב הראשון?", "he": ""}, {"text": "Why now?", "he": ""}]
    [card] = events(out, "card")
    assert (card["text"], card["lang"], card["he"]) == ("איך חילקתם את המניות?", "he", "")


def test_in_an_english_call_questions_keep_their_language_and_translation():
    reply = {"card": None, "goals": [], "terms": [],
             "questions": [{"text": "Who raised the first round?", "he": "מי גייס את הסבב הראשון?"}]}
    orch, fake, out, *_ = make([reply])
    orch.on_line(line(1, "We raised our first round from two angels last spring", lang="en"), "en")
    orch.pump()
    [shown] = events(out, "questions")
    assert shown["items"] == [{"text": "Who raised the first round?", "he": "מי גייס את הסבב הראשון?"}]


def test_a_note_asks_at_once_and_its_questions_replace_the_shown_ones():
    steered = {"card": {"kind": "question", "text": "מי מחליט?", "lang": "he", "he": ""}, "goals": [], "terms": [],
               "questions": ["איך חילקתם את המניות?"]}
    clock = Clock()
    orch, fake, out, *_ = make([ASK, steered], clock=clock)
    orch.on_line(line(1, t=clock.t), "he")
    orch.pump()
    clock.t += 5
    orch.add_context("תתמקד בשותפים, בלי שאלות על סייבר")
    assert orch.pump() is True
    assert "mode: note" in sent(fake, 1) and "my_notes:\n- תתמקד בשותפים, בלי שאלות על סייבר\n" in sent(fake, 1)
    assert [texts(e) for e in events(out, "questions")] == [ASK["questions"], steered["questions"]]
    assert events(out, "card") == []
    assert orch.pump() is False


def test_a_note_asks_nothing_while_ask_is_closed_or_without_live_questions():
    orch, fake, out, *_ = make()
    orch.set_sections(ask=False, terms=True)
    orch.add_context("בלי שאלות על סייבר")
    assert orch.pump() is False
    plain, fake2, *_ = make(call_type=parse_call_type("general", "cards: tip\ngoals:\n---\nA call.\n"))
    plain.add_context("בלי שאלות על סייבר")
    assert plain.pump() is False


def test_the_rules_say_how_notes_steer_and_that_the_summary_keeps_everything():
    from callcoach.prompts import LECTURE_RULES, RECAP_RULES, STEP_RULES, SUMMARY_RULES
    assert "mode note" in STEP_RULES and "לא בכיוון: <a question>" in STEP_RULES
    assert "\"בכיוון: <a question>\" means that direction is right" in STEP_RULES
    assert "a later note wins" in STEP_RULES
    assert "Take it from an open goal only when the talk is stuck" in STEP_RULES
    for rules in (SUMMARY_RULES, LECTURE_RULES, RECAP_RULES):
        assert "Never leave out what was said because of them" in rules


def translations(out):
    return [(e["id"], e["he"]) for e in events(out, "translation")]


def translate_calls(fake):
    return [m for kind, m in fake.calls if kind == "translate"]


def test_the_other_sides_english_lines_are_translated_while_the_panel_asks():
    orch, fake, out, clock, used = make()
    orch.on_line(line(1, "We approve every request by hand", lang="en"), "en")
    orch.set_translate(True)
    orch.on_line(line(2, "Last week a developer waited two days", lang="en", t=1010), "en")
    orch.on_line(line(3, "how many people approve?", channel="me", lang="en", t=1014), "en")
    orch.on_line(line(4, "זה לוקח לנו יומיים", lang="he", t=1016), "en")
    assert wait_for(lambda: translations(out))
    time.sleep(0.1)
    assert translations(out) == [(2, "בעברית: Last week a developer waited two days")]
    [message] = translate_calls(fake)
    before, asked = message.split("## Translate")
    assert "them (en): We approve every request by hand" in before
    assert asked.strip() == "2: Last week a developer waited two days"
    assert used == ["fake"]


def test_lines_said_while_a_translation_runs_wait_and_only_the_newest_go_next():
    gate, entered = threading.Event(), threading.Event()

    class Held(FakeProvider):
        def translate(self, system, message):
            if not self.calls:
                entered.set()
                gate.wait(5)
            return super().translate(system, message)

    fake = Held([NONE] * 10)
    orch, _, out, *_ = make(provider=fake)
    orch.set_translate(True)
    orch.on_line(line(1, "first line", lang="en"), "en")
    assert entered.wait(5)
    for i in range(2, 7):
        orch.on_line(line(i, f"line number {i}", lang="en", t=1000.0 + i), "en")
    gate.set()
    assert wait_for(lambda: len(translations(out)) == 4)
    first, second = translate_calls(fake)
    assert first.split("## Translate")[1].strip() == "1: first line"
    assert second.split("## Translate")[1].strip() == "4: line number 4\n5: line number 5\n6: line number 6"
    assert [i for i, _ in translations(out)] == [1, 4, 5, 6]


def test_no_translation_when_it_is_off_without_an_agent_or_after_the_call():
    orch, fake, out, *_ = make()
    orch.set_translate(True)
    orch.set_translate(False)
    orch.on_line(line(1, "We approve every request by hand", lang="en"), "en")
    bare, _, bare_out, *_ = make(provider=None)
    bare.set_translate(True)
    bare.on_line(line(1, "We approve every request by hand", lang="en"), "en")
    orch.set_translate(True)
    orch.close()
    orch.on_line(line(2, "Last week a developer waited two days", lang="en"), "en")
    time.sleep(0.1)
    assert translate_calls(fake) == [] and translations(out) == [] and translations(bare_out) == []


def test_a_failed_translation_is_noted_and_the_next_line_translates(tmp_path):
    fake = FakeProvider([NONE] * 10, errors=[ProviderUnavailable("overloaded")])
    orch, _, out, *_ = make(provider=fake, log=tmp_path / "events.log")
    orch.set_translate(True)
    orch.on_line(line(1, "We approve every request by hand", lang="en"), "en")
    assert wait_for(lambda: len(translate_calls(fake)) == 1)
    log = tmp_path / "events.log"
    assert wait_for(lambda: log.exists() and notes(log, "translation_failed"))
    orch.on_line(line(2, "Last week a developer waited two days", lang="en", t=1010), "en")
    assert wait_for(lambda: translations(out))
    assert translations(out) == [(2, "בעברית: Last week a developer waited two days")]
    assert notes(tmp_path / "events.log", "translation_failed")[0]["error"] == "ProviderUnavailable"


def test_a_recording_sends_the_model_nothing_whatever_it_is_asked():
    orch, fake, out, clock, used = make([QUESTION] * 5, coach=False)
    orch.set_translate(True)
    orch.set_sections(True, True)
    orch.on_line(line(1, "We use Okta for access today", lang="en"), "en")
    orch.on_line(line(2), "he")
    orch.ask_now(clock.t)
    orch.recap()
    orch.recap(since=True)
    orch.terms_now()
    orch.add_context("דנה היא ה-CISO")
    while orch.pump():
        pass
    time.sleep(0.2)
    assert fake.calls == [] and used == []
    assert not events(out, "card") and not events(out, "translation") and not events(out, "questions")
    assert all(not e["ok"] for e in events(out, "recap"))


def test_after_the_coach_turns_on_the_live_step_runs_from_that_moment():
    orch, fake, out, clock, _ = make([QUESTION], coach=False)
    orch.on_line(line(1, "את זה אמרתי לפני שהמאמן הופעל"), "he")
    orch.set_coach(True)
    orch.set_sections(True, True)
    orch.on_line(line(2), "he")
    assert orch.pump()
    message = sent(fake)
    assert "לפני שהמאמן הופעל" not in message and LONG in message


def test_the_recap_reads_the_whole_call_even_the_part_before_the_coach():
    orch, fake, out, clock, _ = make([NONE], coach=False)
    orch.on_line(line(1, "את זה אמרתי לפני שהמאמן הופעל"), "he")
    orch.set_coach(True)
    orch.recap()
    deadline = time.time() + 5
    while not events(out, "recap") and time.time() < deadline:
        time.sleep(0.02)
    recap_messages = [m for kind, m in fake.calls if kind == "recap"]
    assert recap_messages and "לפני שהמאמן הופעל" in recap_messages[0]


def test_translation_alone_sends_only_translation():
    orch, fake, out, clock, _ = make([NONE] * 3)
    orch.set_sections(False, False)
    orch.set_translate(True)
    orch.on_line(line(1, "We use Okta for access today", lang="en"), "en")
    while orch.pump():
        pass
    deadline = time.time() + 5
    while not [k for k, _ in fake.calls if k == "translate"] and time.time() < deadline:
        time.sleep(0.02)
    kinds = [kind for kind, _ in fake.calls]
    assert "translate" in kinds and "step" not in kinds


def test_switching_the_coach_off_stops_translation_and_the_live_step():
    orch, fake, out, clock, _ = make([QUESTION] * 3)
    orch.set_coach(False)
    orch.set_translate(True)
    orch.on_line(line(1, "We use Okta for access today", lang="en"), "en")
    orch.on_line(line(2), "he")
    assert not orch.pump()
    time.sleep(0.2)
    assert fake.calls == []


def test_a_goal_checked_by_hand_stays_as_I_set_it():
    orch, fake, out, clock, _ = make([{**NONE, "goals": [{"id": "g1", "done": False}]}])
    orch.set_goal("g1", True)
    orch.on_line(line(1), "he")
    assert orch.pump()
    assert orch.goal_list()[0].done and "[g1] done (by me): מי מחליט" in sent(fake)
    assert events(out, "goals")[-1]["goals"][0]["done"] is True


def test_focus_asks_for_questions_toward_the_goal_at_once():
    reply = {**NONE, "questions": [{"text": "מי מאשר היום גישה?", "he": ""}]}
    orch, fake, out, clock, _ = make([reply])
    orch.focus_goal("g1")
    assert orch.pump()
    message = sent(fake)
    assert "mode: focus" in message and "focus_goal: [g1] מי מחליט" in message
    assert texts(events(out, "questions")[-1]) == ["מי מאשר היום גישה?"]
    assert not events(out, "card")


def test_a_reached_goal_ends_its_focus():
    orch, fake, out, clock, _ = make([{**NONE, "goals": [{"id": "g1", "done": True}]}, NONE])
    orch.focus_goal("g1")
    assert orch.pump()
    orch.on_line(line(2), "he")
    assert orch.pump()
    assert "focus_goal: (none)" in sent(fake, 1)


def test_focus_on_a_goal_that_is_done_or_unknown_is_ignored():
    orch, fake, out, clock, _ = make([NONE])
    orch.set_goal("g2", True)
    orch.focus_goal("g2")
    orch.focus_goal("nope")
    assert not orch.pump()


def test_list_only_keeps_the_local_names_and_asks_the_model_for_none():
    reply = {"card": None, "goals": [], "terms": [{"term": "SCIM", "note": "תקן", "kind": "term"}], "questions": []}
    orch, fake, out, *_ = make([reply, reply], brief_terms=BRIEF_TERMS)
    orch.set_sections(True, True, False, True)
    assert orch.wants_partials() is True
    orch.on_line(line(1, "we need SOC 2 and SCIM for this", lang="en"), "en")
    assert orch.pump() is True
    assert "sections: ask on, terms off, now off" in sent(fake)
    assert [t["term"] for t in events(out, "term")] == ["SOC 2"]


def test_list_only_with_questions_and_the_now_line_off_sends_no_live_request():
    orch, fake, out, *_ = make([NONE], brief_terms=BRIEF_TERMS)
    orch.set_sections(False, True, False, True)
    orch.on_line(line(1, "we looked at Nimbus Labs last year", lang="en"), "en")
    assert orch.pump() is False and fake.calls == []
    assert [t["term"] for t in events(out, "term")] == ["Nimbus Labs"]
    orch.set_sections(False, True, False, False)
    orch.on_line(line(2, "ומה עם התקציב לשנה הבאה"), "he")
    assert orch.pump() is True and "sections: ask off, terms on, now off" in sent(fake)


def test_list_only_still_answers_the_search_i_ask_for():
    fake = FakeProvider([OKTA_TERM], spotted=SPOTTED)
    orch, _, out, _ = with_term(fake)
    orch.set_sections(False, True, False, True)
    orch.terms_now()
    assert wait_for(lambda: spotted(out))
    assert spotted(out) == [(True, [2, 1])] and spot_calls(fake)


def test_the_sections_note_records_list_only(tmp_path):
    orch, *_ = make(log=tmp_path / "events.log")
    orch.set_sections(True, True, False, True)
    [note] = notes(tmp_path / "events.log", "sections")
    assert (note["terms"], note["list_only"]) == (True, True)
