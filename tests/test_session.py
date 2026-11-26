import io
import json
import threading
import time

import pytest
from audio_helpers import has_ffmpeg, loud, write_stereo
from test_engine import ME_AUDIO, THEM_AUDIO, FakeTranscriber

from callcoach.cards import Question
from callcoach.config import GLOSSARY_PROMPT
from callcoach.context import Goal
from callcoach.engine import CallEngine
from callcoach.protocol import Emitter
from callcoach.provider_factory import Account
from callcoach.providers import DEMO_PREP, FakeProvider, ProviderUnavailable
from callcoach.session import Session
from callcoach.sources import FileSource

pytestmark = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")

START = {"type": "start", "call_type": "validation", "lang": "he", "level": "normal", "text": "דנה מ-Acme, בקשות גישה",
         "brief": "", "length_min": 30, "goals": [{"id": "g1", "text": "מי מחליט", "done": False}],
         "glossary": ["Okta"], "name": "demo", "title": "", "questions": []}
SUMMARIZE = {"type": "stop", "summary": True}


class Servers(FakeTranscriber):
    started = stopped = False
    glossary = ()

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def set_glossary(self, terms):
        self.glossary = tuple(terms)


class SlowServers(Servers):
    def start(self):
        time.sleep(1.0)
        super().start()


class FailingServers(Servers):
    def start(self):
        time.sleep(1.0)
        raise FileNotFoundError("whisper-server not found")


class HeldFile(FileSource):
    """The call's audio. When it has ended, its stop waits until the session has read the script's `then`, so that
    command is the stop the call ends with, as the app's stop is."""

    def __init__(self, path):
        super().__init__(path, realtime=False)
        self.stopping = threading.Event()
        self.read = threading.Event()

    def stop(self):
        super().stop()
        self.stopping.set()
        self.read.wait(30)


class Script:
    """stdin for the session: the given lines, then `then` once the call is ending (with a HeldFile), then EOF once
    the call is finalized (or at once with no call)."""

    def __init__(self, out, *commands, wait_for="finalized", then=None, held=None):
        self.out = out
        self.lines = [line(c) if isinstance(c, dict) else c for c in commands]
        self.wait_for = wait_for
        self.then = line(then) if then else None
        self.held = held

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        if self.then:
            self.held.stopping.wait(30)
            then, self.then = self.then, None
            return then
        if self.held:
            self.held.read.set()
        deadline = time.monotonic() + 30
        while self.wait_for and f'"{self.wait_for}"' not in self.out.getvalue() and time.monotonic() < deadline:
            time.sleep(0.02)
        return ""


def line(command: dict) -> str:
    return json.dumps(command, ensure_ascii=False) + "\n"


def make(tmp_path, *commands, provider="fake", servers=None, account=None, account_error="", wait_for="finalized",
         environ=None, then=None):
    """A session over the test call. `then` is the command the call ends with when its audio is over (`SUMMARIZE`,
    say); without it the call ends by itself, with no stop."""
    wav = tmp_path / "in.wav"
    write_stereo(wav, ME_AUDIO, THEM_AUDIO)
    held = HeldFile(wav) if then else None
    out = io.StringIO()
    provider = FakeProvider() if provider == "fake" else provider
    servers = servers or Servers()

    def make_transcriber(log_dir):
        if isinstance(servers, Exception):
            raise servers
        return servers

    session = Session(
        data=tmp_path / "data", emitter=Emitter(out), account=account or Account("fake", "fake"),
        provider=provider, account_error=account_error, make_transcriber=make_transcriber,
        make_sources=lambda prep, emitter, log_dir: ([held or FileSource(wav, realtime=False)], lambda: True),
        make_engine=lambda **kw: CallEngine(new_vad=lambda: loud, **kw),
        stdin=Script(out, *commands, wait_for=wait_for, then=then, held=held), run_dir=tmp_path / "run", prices={},
        environ={} if environ is None else environ)
    return session, out, provider, servers


def events(out):
    return [json.loads(l) for l in out.getvalue().splitlines()]


def types(out):
    return [e["type"] for e in events(out)]


def test_a_whole_call_through_serve(tmp_path):
    session, out, fake, servers = make(tmp_path, {"type": "account_check"}, START, then=SUMMARIZE)
    assert session.run() == 0
    es, ts = events(out), types(out)
    assert ts[:3] == ["hello", "status", "usage"] and es[1]["state"] == "warming" and es[0]["protocol"] == 6
    assert ts[-4:] == ["goals", "usage", "summary_ready", "stopped"] and ts.count("stopped") == 1
    order = [ts.index(t) for t in ("account", "ready", "line", "finalized")]
    assert order == sorted(order)
    assert next(e for e in es if e["type"] == "account")["ok"] is True
    summary = next(e for e in es if e["type"] == "summary_ready")
    folder = tmp_path / "data" / "calls"
    call = next(folder.iterdir())
    assert summary["ok"] and summary["path"] == str(call / "summary.md")
    assert "## שימוש" in (call / "summary.md").read_text(encoding="utf-8")
    assert json.loads((call / "terms.json").read_text(encoding="utf-8")) == []
    assert json.loads((call / "usage.json").read_text(encoding="utf-8"))["requests"] >= 3
    assert (call / "events.log").exists() and not any((tmp_path / "run").iterdir())
    assert servers.started and servers.stopped and servers.glossary == (("Okta",) if GLOSSARY_PROMPT else ())
    assert [kind for kind, _ in fake.calls][0] == "check" and fake.calls[-1][0] == "summarize"
    assert "prepare" not in [kind for kind, _ in fake.calls]
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta.keys() == {"started", "summary_kind", "summary_local", "call_type", "coach_from", "goals", "notes",
                           "glossary"} \
        and meta["summary_kind"] in ("call", "lecture") and meta["summary_local"] is False
    assert (es[1]["hearing"], es[1]["me_voice"]) == (True, False)
    assert fake.closed


def test_eof_before_start_ends_with_stopped(tmp_path):
    session, out, fake, servers = make(tmp_path, {"type": "account_check"}, wait_for="")
    assert session.run() == 0
    assert types(out)[-1] == "stopped" and "ready" not in types(out)
    assert not (tmp_path / "data" / "calls").exists() or not any((tmp_path / "data" / "calls").iterdir())
    assert servers.stopped
    statuses = [e["state"] for e in events(out) if e["type"] == "status"]
    assert statuses[0] == "warming" and "idle" in statuses


def test_prepare_is_no_longer_a_command(tmp_path):
    prepare = {"type": "prepare", "call_type": "validation", "lang": "he", "level": "normal", "text": "x",
               "briefs": [], "length_min": None}
    session, out, fake, _ = make(tmp_path, prepare, wait_for="")
    session.run()
    assert [e["code"] for e in events(out) if e["type"] == "error"] == ["bad_command"] and fake.calls == []


def test_servers_that_do_not_start_still_allow_a_call(tmp_path):
    session, out, _, _ = make(tmp_path, START, servers=FileNotFoundError("whisper-server not found"))
    session.run()
    es = events(out)
    assert [e["code"] for e in es if e["type"] == "error"] == ["transcription_start_failed"]
    assert next(e for e in es if e["type"] == "finalized")["ok"] and "line" not in types(out)
    assert types(out)[-1] == "stopped"


def test_commands_in_the_wrong_state_are_bad_commands(tmp_path):
    session, out, _, _ = make(tmp_path, {"type": "set_level", "level": "quiet"}, "not json\n", wait_for="")
    session.run()
    assert [e["code"] for e in events(out) if e["type"] == "error"] == ["bad_command", "bad_command"]


def test_an_account_problem_shows_as_auth(tmp_path):
    session, out, _, _ = make(tmp_path, {"type": "account_check"}, provider=None,
                              account=Account("claude", "home"), account_error="account home is not logged in yet",
                              wait_for="")
    session.run()
    es = events(out)
    account = next(e for e in es if e["type"] == "account")
    assert account == {**account, "ok": False, "provider": "claude", "name": "home", "via_claude_code": True,
                       "message": "account home is not logged in yet"}
    assert {e["agent"] for e in es if e["type"] == "status"} == {"auth"}


def test_the_live_servers_write_their_notes_to_the_log(tmp_path, monkeypatch):
    import callcoach.transcriber as tr
    from callcoach.session import live_transcriber

    seen = {}
    monkeypatch.setattr(tr, "default_servers", lambda log_dir, vad=True, on_note=None: seen.update(
        log_dir=log_dir, on_note=on_note) or {})
    emitter = Emitter(io.StringIO())
    live_transcriber(emitter)(tmp_path)
    assert seen == {"log_dir": tmp_path, "on_note": emitter.note}


def test_context_added_in_the_call_reaches_the_agent_and_the_summary(tmp_path):
    context = {"type": "add_context", "text": "דנה מנהלת IT ב-Acme"}
    session, out, fake, _ = make(tmp_path, START, context, then=SUMMARIZE)
    assert session.run() == 0
    summary = [m for kind, m in fake.calls if kind == "summarize"]
    assert summary and "דנה מנהלת IT ב-Acme" in summary[0]
    log = next((tmp_path / "data" / "calls").iterdir()) / "events.log"
    assert '"context_added"' in log.read_text(encoding="utf-8")


def write_people(root):
    folder = root / "data" / "knowledge"
    folder.mkdir(parents=True)
    (folder / "people.md").write_text("# חברות\n\n## Shalom Labs\nסוג: חברה\nכתיבים: שלומכם\nתקציר: חברה לדוגמה\n"
                                      "שלנו:\n- פגישה ראשונה, דנה, 2026-02-01, מקור: \"יומן\" (https://example.com)\n",
                                      encoding="utf-8")


SHALOM = {"term": "Shalom Labs", "note": "חברה לדוגמה", "kind": "company", "lang": "he", "known": True}
OKTA = {"term": "Okta", "note": "שירות התחברות לארגונים", "kind": "product", "lang": "he", "known": False}


def okta_reply(message):
    return {"card": None, "goals": [], "terms": [{k: OKTA[k] for k in ("term", "note", "kind")}], "questions": []}


def test_a_name_from_the_knowledge_folder_shows_in_the_call_and_the_summary(tmp_path):
    write_people(tmp_path)
    session, out, fake, _ = make(tmp_path, START, then=SUMMARIZE)
    assert session.run() == 0
    es = events(out)
    [term] = [e for e in es if e["type"] == "term" and e["term"] == "Shalom Labs"]
    assert term["known"] is True and term["ours"] == ["פברואר 2026: פגישה ראשונה, דנה"]
    summary = (next((tmp_path / "data" / "calls").iterdir()) / "summary.md").read_text(encoding="utf-8")
    assert "- Shalom Labs: חברה לדוגמה\n  - שלנו: פגישה ראשונה, דנה, 2026-02-01, מקור:" in summary


def test_every_term_of_the_call_is_written_to_terms_json(tmp_path):
    write_people(tmp_path)
    session, out, *_ = make(tmp_path, START, provider=FakeProvider(replies=okta_reply))
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    text = (call / "terms.json").read_text(encoding="utf-8")
    assert json.loads(text) == [SHALOM, OKTA]
    assert "חברה לדוגמה" in text and not (call / "terms.json.tmp").exists()


class SummaryDown(FakeProvider):
    """The summary request fails; it keeps what terms.json held when the request was made."""

    def __init__(self, calls, **kw):
        super().__init__(**kw)
        self.calls_folder = calls
        self.terms_then = None

    def summarize(self, system, message):
        path = next(self.calls_folder.iterdir()) / "terms.json"
        self.terms_then = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        raise ProviderUnavailable("busy")


def test_terms_json_is_written_before_the_summary_and_kept_when_it_fails(tmp_path):
    fake = SummaryDown(tmp_path / "data" / "calls", replies=okta_reply)
    session, out, *_ = make(tmp_path, START, provider=fake, then=SUMMARIZE)
    assert session.run() == 0
    assert any(e["type"] == "error" and e["code"] == "summary_failed" for e in events(out))
    call = next((tmp_path / "data" / "calls").iterdir())
    assert fake.terms_then == [OKTA]
    assert json.loads((call / "terms.json").read_text(encoding="utf-8")) == [OKTA]


def test_a_terms_file_that_cannot_be_written_still_lets_the_summary_come(tmp_path, monkeypatch):
    import callcoach.session as session_mod

    def disk_full(folder, terms):
        raise OSError("No space left on device")

    monkeypatch.setattr(session_mod, "write_terms", disk_full)
    session, out, *_ = make(tmp_path, START, then=SUMMARIZE)
    assert session.run() == 0
    assert next(e for e in events(out) if e["type"] == "summary_ready")["ok"]
    log = next((tmp_path / "data" / "calls").iterdir()) / "events.log"
    assert '"terms_failed"' in log.read_text(encoding="utf-8")


def test_context_before_the_call_is_a_bad_command(tmp_path):
    session, out, *_ = make(tmp_path, {"type": "add_context", "text": "x"}, wait_for=None)
    assert session.run() == 0
    assert any(e["type"] == "error" and e["code"] == "bad_command" for e in events(out))


def test_partials_run_only_when_there_is_a_knowledge_file(tmp_path):
    def partial_notes(root):
        root.mkdir(parents=True, exist_ok=True)
        session, out, *_ = make(root, START)
        assert session.run() == 0
        log = next((root / "data" / "calls").iterdir()) / "events.log"
        return log.read_text(encoding="utf-8").count('"kind": "partial"')

    assert partial_notes(tmp_path / "without") == 0
    folder = tmp_path / "with" / "data" / "knowledge"
    folder.mkdir(parents=True)
    (folder / "people.md").write_text("## Wiz\nסוג: חברה\nתקציר: אבטחת ענן\n", encoding="utf-8")
    assert partial_notes(tmp_path / "with") >= 1


def test_the_title_and_the_brief_go_into_meta_json(tmp_path):
    folder = tmp_path / "data" / "briefs"
    folder.mkdir(parents=True)
    (folder / "2026-11-17-acme.md").write_text("---\ntitle: שיחה עם Acme\n---\n## רקע\nAcme uses Okta.\n",
                                               encoding="utf-8")
    start = {**START, "title": "שיחה עם Acme", "brief": "2026-11-17-acme.md"}
    session, out, fake, _ = make(tmp_path, start)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "שיחה עם Acme" and meta["brief"] == "2026-11-17-acme.md" and "started" in meta
    assert not (call / "meta.json.tmp").exists()
    assert "Acme uses Okta." in fake.system and "title: שיחה עם Acme" not in fake.system


def test_a_recording_asks_the_model_nothing_and_writes_no_summary(tmp_path):
    start = {**START, "record_only": True, "title": "שיחה עם דנה", "questions": ["מה גרם לכם לחפש פתרון?"]}
    session, out, fake, _ = make(tmp_path, {"type": "account_check"}, start)
    assert session.run() == 0
    es, ts = events(out), types(out)
    assert "line" in ts and "card" not in ts and "questions" not in ts
    assert [kind for kind, _ in fake.calls] == ["check"]
    call = next((tmp_path / "data" / "calls").iterdir())
    summary = next(e for e in es if e["type"] == "summary_ready")
    assert not summary["ok"] and summary["message"] == "בלי סיכום" and summary["path"] == str(call / "summary.md")
    assert not (call / "summary.md").exists() and (call / "transcript.md").exists()
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["record_only"] is True and meta["title"] == "שיחה עם דנה" and "coach_from" not in meta


def test_the_coach_switched_on_in_a_recording_records_when_and_summarizes(tmp_path):
    session, out, fake, _ = make(tmp_path, {**START, "record_only": True}, {"type": "set_coach", "on": True},
                                 then=SUMMARIZE)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["record_only"] is True and meta["coach_from"] >= 0
    assert fake.calls[-1][0] == "summarize" and (call / "summary.md").exists()


def test_stop_without_a_summary_asks_for_none_and_still_counts_the_usage(tmp_path):
    session, out, fake, _ = make(tmp_path, START, {"type": "stop", "summary": False})
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    summary = next(e for e in events(out) if e["type"] == "summary_ready")
    assert "summarize" not in [kind for kind, _ in fake.calls]
    assert not summary["ok"] and summary["message"] == "בלי סיכום" and (call / "usage.json").exists()


@pytest.mark.parametrize("wait_for", ["finalized", ""], ids=["the call ends by itself", "the end of stdin ends it"])
def test_a_call_with_the_coach_that_ends_without_a_stop_writes_no_summary(tmp_path, wait_for):
    session, out, fake, _ = make(tmp_path, START, wait_for=wait_for)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    summary = next(e for e in events(out) if e["type"] == "summary_ready")
    assert "summarize" not in [kind for kind, _ in fake.calls] and not (call / "summary.md").exists()
    assert not summary["ok"] and summary["message"] == "בלי סיכום" and (call / "usage.json").exists()


def test_a_call_stopped_without_a_summary_keeps_its_notes_in_meta(tmp_path):
    session, out, fake, _ = make(tmp_path, START, {"type": "stop", "summary": False})
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["notes"] == START["text"] and "summary_kind" not in meta


def test_the_coach_without_an_account_keeps_recording(tmp_path):
    session, out, _, _ = make(tmp_path, {**START, "record_only": True}, {"type": "set_coach", "on": True},
                              provider=None)
    assert session.run() == 0
    es, ts = events(out), types(out)
    error = next(e for e in es if e["type"] == "error")
    assert error["code"] == "no_agent_account" and not error["ends_call"]
    assert "finalized" in ts and "line" in ts


def test_the_goals_and_the_call_type_go_into_meta_json(tmp_path):
    session, out, _, _ = make(tmp_path, START)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["call_type"] == "validation" and meta["coach_from"] == 0
    assert [g["text"] for g in meta["goals"]] == ["מי מחליט"]
    assert meta["glossary"] == ["Okta"]


def test_a_call_that_is_saved_starts_its_final_pass_after_the_summary(tmp_path):
    session, out, _, _ = make(tmp_path, START, then=SUMMARIZE)
    seen = []
    session.after_call = lambda folder: seen.append((folder, types(out)[-1]))
    assert session.run() == 0
    [(folder, last)] = seen
    assert folder == next((tmp_path / "data" / "calls").iterdir()) and last == "summary_ready"
    assert types(out)[-1] == "stopped"


def test_a_final_pass_that_cannot_start_is_noted_and_the_call_still_ends(tmp_path):
    session, out, _, _ = make(tmp_path, START)

    def boom(folder):
        raise OSError("no such python")

    session.after_call = boom
    assert session.run() == 0
    assert types(out)[-1] == "stopped" and "error" not in types(out)
    log = next((tmp_path / "data" / "calls").iterdir()) / "events.log"
    assert '"after_call_failed"' in log.read_text(encoding="utf-8")


def test_the_opening_questions_show_at_once(tmp_path):
    start = {**START, "questions": ["מה גרם לכם לחפש פתרון עכשיו?"]}
    for provider in ("fake", None):
        root = tmp_path / str(provider)
        root.mkdir()
        session, out, *_ = make(root, start, provider=provider)
        assert session.run() == 0
        first = next(e for e in events(out) if e["type"] == "questions")
        assert first["items"] == [{"text": "מה גרם לכם לחפש פתרון עכשיו?", "he": ""}]
        assert (first["lang"], first["trigger_line_ids"]) == ("he", [])
        assert types(out).index("questions") < types(out).index("line")


def test_an_unknown_term_is_answered_at_once(tmp_path):
    session, out, fake, _ = make(tmp_path, START, {"type": "explain_term", "id": 99})
    assert session.run() == 0
    [detail] = [e for e in events(out) if e["type"] == "term_detail"]
    assert detail["id"] == 99 and detail["ok"] is False and detail["text"]
    assert "explain" not in [kind for kind, _ in fake.calls]


def test_a_start_in_auto_starts_the_engine_in_hebrew(tmp_path):
    seen = {}

    def make_engine(**kw):
        seen["start_lang"] = kw["start_lang"]
        return CallEngine(new_vad=lambda: loud, **kw)

    session, out, *_ = make(tmp_path, {**START, "lang": "auto"})
    session.make_engine = make_engine
    assert session.run() == 0
    assert seen == {"start_lang": "he"}
    assert {e["lang"] for e in events(out) if e["type"] == "questions"} == {"he"}


class Gate(FakeProvider):
    """prepare waits until the test opens the gate."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gate = threading.Event()

    def prepare(self, system, message, schema):
        assert self.gate.wait(10)
        return super().prepare(system, message, schema)


def agent_for(tmp_path, provider, **prep):
    from callcoach.context import Prep, ensure_data, load_call_type
    from callcoach.session import CallAgent
    from callcoach.usage import CallUsage

    data = tmp_path / "data"
    ensure_data(data)
    prep = Prep(**{"call_type": "validation", "text": "דנה מ-Acme", **prep})
    out = io.StringIO()
    agent = CallAgent(provider=provider, emitter=Emitter(out, log=tmp_path / "events.log"), data=data, prep=prep,
                      call_type=load_call_type(data, prep.call_type), usage=CallUsage(), prices={})
    return agent, out


def wait_for(out, needle, timeout=10.0):
    deadline = time.monotonic() + timeout
    while needle not in out.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    return needle in out.getvalue()


def goals_events(out):
    return [e["goals"] for e in events(out) if e["type"] == "goals"]


def questions_events(out):
    return [(e["items"], e["lang"], e["trigger_line_ids"]) for e in events(out) if e["type"] == "questions"]


def background_done():
    for thread in threading.enumerate():
        if thread.name == "goals":
            thread.join(10)


def test_goals_come_in_the_background_and_the_call_does_not_wait(tmp_path):
    fake = Gate()
    agent, out = agent_for(tmp_path, fake)
    agent.begin(time.time(), tmp_path)
    try:
        assert [g.id for g in agent.orchestrator.goal_list()] == ["g1", "g2", "g3"]
        assert goals_events(out) == []
        fake.gate.set()
        assert wait_for(out, '"goals"')
        [goals] = goals_events(out)
        assert [g["text"] for g in goals] == [g.text for g in agent.orchestrator.goal_list()]
        assert goals[0] == {"id": "b1", "text": "להבין איך מנהלים היום בקשות גישה", "done": False}
        assert [g["id"] for g in goals] == [f"b{i}" for i in range(1, len(goals) + 1)]
        prepared = [m for kind, m in fake.calls if kind == "prepare"]
        assert len(prepared) == 1 and "דנה מ-Acme" in prepared[0]
        assert agent.usage.total().requests == 1
    finally:
        agent.orchestrator.close()


def test_a_brief_alone_is_enough_for_goals(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, text="", brief="acme.md")
    (tmp_path / "data" / "briefs" / "acme.md").write_text("## רקע\nAcme uses Okta.\n", encoding="utf-8")
    agent.begin(time.time(), tmp_path)
    try:
        assert wait_for(out, '"goals"')
        assert "Acme uses Okta." in next(m for kind, m in fake.calls if kind == "prepare")
    finally:
        agent.orchestrator.close()


@pytest.mark.parametrize("prep", [
    {"text": ""},
    {"text": "", "brief": "missing.md"},
    {"goals": (Goal("g1", "מי מחליט"),)},
])
def test_goals_are_not_asked_for_without_something_to_work_from(tmp_path, prep):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, **prep)
    agent.begin(time.time(), tmp_path)
    agent.orchestrator.close()
    time.sleep(0.05)
    assert [kind for kind, _ in fake.calls if kind == "prepare"] == [] and goals_events(out) == []
    assert [items for items, *_ in questions_events(out) if items] == []


@pytest.mark.parametrize("lang, first", [("he", "he"), ("en", "en"), ("auto", "he")])
def test_the_background_questions_show_when_the_call_has_none(tmp_path, lang, first):
    from callcoach.providers import DEMO_PREP

    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, lang=lang)
    agent.begin(time.time(), tmp_path)
    try:
        background_done()
        assert questions_events(out) == [([], first, []), (DEMO_PREP["questions"], "he", [])]
        assert agent.orchestrator.questions == tuple(Question(**q) for q in DEMO_PREP["questions"])
    finally:
        agent.orchestrator.close()


def test_the_background_questions_never_replace_the_opening_ones(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, questions=(Question("מה גרם לכם לחפש פתרון עכשיו?"),))
    agent.begin(time.time(), tmp_path)
    try:
        background_done()
        assert len(goals_events(out)) == 1
        assert questions_events(out) == [([{"text": "מה גרם לכם לחפש פתרון עכשיו?", "he": ""}], "he", [])]
    finally:
        agent.orchestrator.close()


def test_the_background_questions_never_replace_live_ones(tmp_path):
    fake = Gate()
    agent, out = agent_for(tmp_path, fake)
    agent.begin(time.time(), tmp_path)
    try:
        with agent.orchestrator.cond:
            agent.orchestrator._emit_questions((Question("מה היה הכי קשה?"),), [1], time.time())
        fake.gate.set()
        background_done()
        assert len(goals_events(out)) == 1
        assert [items for items, *_ in questions_events(out)] == [[], [{"text": "מה היה הכי קשה?", "he": ""}]]
    finally:
        agent.orchestrator.close()


def test_goals_that_fail_keep_the_call_type_goals_and_end_nothing(tmp_path):
    fake = FakeProvider(errors=[ProviderUnavailable("busy")])
    agent, out = agent_for(tmp_path, fake)
    agent.begin(time.time(), tmp_path)
    try:
        log, deadline = tmp_path / "events.log", time.monotonic() + 10
        while "goals_failed" not in log.read_text(encoding="utf-8") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "goals_failed" in log.read_text(encoding="utf-8")
        assert [g.id for g in agent.orchestrator.goal_list()] == ["g1", "g2", "g3"]
        assert [e for e in events(out) if e["type"] in ("error", "goals")] == []
    finally:
        agent.orchestrator.close()


def test_a_start_with_ask_off_sends_no_goals_request_and_no_opening_questions(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, sections=(False, True), questions=(Question("מה גרם לכם לחפש פתרון?"),))
    agent.begin(time.time(), tmp_path)
    try:
        background_done()
        assert agent.orchestrator.sections == {"ask": False, "terms": True, "now": False}
        assert [kind for kind, _ in fake.calls if kind == "prepare"] == []
        assert questions_events(out) == [] and agent.orchestrator.questions == (Question("מה גרם לכם לחפש פתרון?"),)
    finally:
        agent.orchestrator.close()


def test_a_start_with_ask_on_sends_the_goals_request_and_the_opening_questions(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, sections=(True, False))
    agent.begin(time.time(), tmp_path)
    try:
        background_done()
        assert agent.orchestrator.sections == {"ask": True, "terms": False, "now": False}
        assert len([kind for kind, _ in fake.calls if kind == "prepare"]) == 1
        assert len(goals_events(out)) == 1 and [i for i, *_ in questions_events(out) if i]
    finally:
        agent.orchestrator.close()


def test_a_recording_start_with_sections_keeps_them_and_the_coach_off(tmp_path):
    agent, out = agent_for(tmp_path, FakeProvider(), record_only=True, sections=(True, False))
    agent.begin(time.time(), tmp_path)
    try:
        assert agent.coach is False and agent.orchestrator.sections == {"ask": True, "terms": False, "now": False}
        assert questions_events(out) == []
    finally:
        agent.orchestrator.close()


def test_the_sections_of_start_are_in_place_from_the_first_live_request(tmp_path):
    start = {**START, "sections": {"ask": True, "terms": False}}
    session, out, fake, _ = make(tmp_path, start)
    assert session.run() == 0
    live = [m for kind, m in fake.calls if kind == "step"]
    assert live and all("sections: ask on, terms off" in m for m in live)


def test_a_start_with_bad_sections_is_a_bad_command(tmp_path):
    bad = {**START, "sections": {"ask": 1, "terms": True}}
    session, out, fake, _ = make(tmp_path, bad, wait_for=None)
    assert session.run() == 0
    assert [e["code"] for e in events(out) if e["type"] == "error"] == ["bad_command"]
    assert fake.calls == []


def test_no_agent_means_no_goals_request(tmp_path):
    agent, out = agent_for(tmp_path, None)
    agent.begin(time.time(), tmp_path)
    assert goals_events(out) == [] and agent.orchestrator.thread is None


class Engine:
    def __init__(self):
        self.calls = []

    def set_hearing(self, on):
        self.calls.append(("hearing", on))

    def set_paused(self, on):
        self.calls.append(("paused", on))

    def lock_language(self, lang):
        self.calls.append(("lock", lang))

    def unlock_language(self):
        self.calls.append(("unlock",))


OPENING = (Question("מה גרם לכם לחפש פתרון?"),)
OPENING_SHOWN = ([{"text": "מה גרם לכם לחפש פתרון?", "he": ""}], "he", [])


def a_recording_with_a_brief(tmp_path, provider):
    agent, out = agent_for(tmp_path, provider, record_only=True, text="", brief="acme.md", questions=OPENING)
    (tmp_path / "data" / "briefs" / "acme.md").write_text("## רקע\nAcme uses Okta.\n", encoding="utf-8")
    agent.begin(time.time(), tmp_path)
    return agent, out


def send(agent, *commands):
    engine = Engine()
    for command in commands:
        assert agent.command(engine, command)
    background_done()


def prepared(fake):
    return len([kind for kind, _ in fake.calls if kind == "prepare"])


COACH_ON, COACH_OFF = {"type": "set_coach", "on": True}, {"type": "set_coach", "on": False}
BOTH, TERMS_ONLY, NEITHER = ({"type": "set_sections", "ask": ask, "terms": terms}
                             for ask, terms in ((True, True), (False, True), (False, False)))


def test_the_coach_joining_a_recording_brings_the_opening_questions_and_the_goals_once(tmp_path):
    fake = FakeProvider()
    agent, out = a_recording_with_a_brief(tmp_path, fake)
    try:
        assert questions_events(out) == [] and prepared(fake) == 0
        send(agent, COACH_ON, BOTH)
        assert questions_events(out) == [OPENING_SHOWN]
        assert prepared(fake) == 1 and "Acme uses Okta." in next(m for kind, m in fake.calls if kind == "prepare")
        assert [g["id"] for g in goals_events(out)[-1]] == ["b1", "b2", "b3"]
    finally:
        agent.orchestrator.close()


def test_a_second_join_brings_neither_again(tmp_path):
    fake = FakeProvider()
    agent, out = a_recording_with_a_brief(tmp_path, fake)
    try:
        send(agent, COACH_ON, BOTH)
        send(agent, COACH_OFF, NEITHER, COACH_ON, BOTH, TERMS_ONLY, BOTH)
        assert questions_events(out) == [OPENING_SHOWN] and prepared(fake) == 1 and len(goals_events(out)) == 1
    finally:
        agent.orchestrator.close()


def test_ask_off_at_the_join_brings_them_when_it_opens(tmp_path):
    fake = FakeProvider()
    agent, out = a_recording_with_a_brief(tmp_path, fake)
    try:
        send(agent, COACH_ON, TERMS_ONLY)
        assert questions_events(out) == [] and prepared(fake) == 0
        send(agent, BOTH)
        assert questions_events(out) == [OPENING_SHOWN] and prepared(fake) == 1
    finally:
        agent.orchestrator.close()


def test_the_coach_joining_with_ask_already_open_brings_them_at_once(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, record_only=True, sections=(True, False))
    agent.begin(time.time(), tmp_path)
    try:
        send(agent, COACH_ON)
        assert questions_events(out) == [([], "he", []), (DEMO_PREP["questions"], "he", [])] and prepared(fake) == 1
    finally:
        agent.orchestrator.close()


def test_a_call_that_starts_with_the_coach_gets_them_only_at_the_start(tmp_path):
    fake = FakeProvider()
    agent, out = agent_for(tmp_path, fake, questions=OPENING)
    agent.begin(time.time(), tmp_path)
    try:
        background_done()
        send(agent, COACH_OFF, NEITHER, COACH_ON, BOTH, TERMS_ONLY, BOTH)
        assert questions_events(out) == [OPENING_SHOWN] and prepared(fake) == 1 and len(goals_events(out)) == 1
    finally:
        agent.orchestrator.close()


def test_the_coach_without_an_account_brings_nothing(tmp_path):
    agent, out = a_recording_with_a_brief(tmp_path, None)
    send(agent, COACH_ON, BOTH)
    assert [e["code"] for e in events(out) if e["type"] == "error"] == ["no_agent_account"]
    assert questions_events(out) == [] and goals_events(out) == []


def test_set_language_auto_unlocks_and_explain_term_and_terms_now_reach_the_orchestrator(tmp_path):
    agent, out = agent_for(tmp_path, None)
    agent.begin(time.time(), tmp_path)
    engine = Engine()
    assert agent.command(engine, {"type": "set_language", "lang": "en"})
    assert agent.command(engine, {"type": "set_language", "lang": "auto"})
    assert engine.calls == [("lock", "en"), ("unlock",)]
    assert agent.command(engine, {"type": "explain_term", "id": 1})
    assert agent.command(engine, {"type": "explain_term", "id": 1, "more": True})
    assert agent.command(engine, {"type": "action", "id": "terms_now"})
    assert agent.command(engine, {"type": "check_terms", "terms": ["סקים"]})
    assert agent.command(engine, {"type": "add_screen", "path": str(tmp_path / "a.png"), "text": "הדמו"})
    assert [e["type"] for e in events(out)] == ["term_detail", "term_detail", "terms_now", "terms_now", "screen"]
    assert engine.calls[2:] == [] and agent.orchestrator.context_notes() == ["הדמו"]


def test_the_hearing_switch_the_sections_and_the_recap_reach_the_orchestrator(tmp_path):
    agent, out = agent_for(tmp_path, None)
    agent.begin(time.time(), tmp_path)
    engine = Engine()
    assert agent.command(engine, {"type": "set_hearing", "on": False})
    assert engine.calls == [("hearing", False)] and agent.orchestrator.hearing_me is False
    assert agent.command(engine, {"type": "set_sections", "ask": True, "terms": False})
    assert agent.orchestrator.sections == {"ask": True, "terms": False, "now": False}
    assert agent.command(engine, {"type": "set_sections", "ask": True, "terms": False, "now": True})
    assert agent.orchestrator.sections == {"ask": True, "terms": False, "now": True}
    assert agent.command(engine, {"type": "set_sections", "ask": True, "terms": True, "list_only": True})
    assert agent.orchestrator.list_only and agent.orchestrator.model_sections()["terms"] is False
    assert agent.command(engine, {"type": "set_translate", "on": True}) and agent.orchestrator.translate_on
    assert agent.command(engine, {"type": "recap"})
    [recap] = [e for e in events(out) if e["type"] == "recap"]
    assert (recap["ok"], recap["text"]) == (False, "לא הצלחתי לסכם")


def test_a_pause_reaches_the_engine_and_the_recap_knows_it(tmp_path):
    agent, out = agent_for(tmp_path, None)
    agent.begin(time.time(), tmp_path)
    engine = Engine()
    assert agent.command(engine, {"type": "set_paused", "on": True})
    assert agent.command(engine, {"type": "set_paused", "on": False})
    assert engine.calls == [("paused", True), ("paused", False)]
    rows = agent.orchestrator._paused_rows()
    assert rows[0] == "## Times the recording was paused (neither side was heard)" and len(rows) == 2


def test_a_paused_call_says_so_in_the_transcript_and_the_summary(tmp_path):
    session, out, fake, _ = make(tmp_path, START, {"type": "set_paused", "on": True})
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    assert "מכאן ההקלטה מושהית" in (call / "transcript.md").read_text(encoding="utf-8")
    summarized = [m for kind, m in fake.calls if kind == "summarize"]
    assert not summarized or "# When the recording was paused (neither side was recorded)" in summarized[0]


def test_hearing_and_sections_before_a_call_are_bad_commands(tmp_path):
    session, out, *_ = make(tmp_path, {"type": "set_hearing", "on": False},
                            {"type": "set_sections", "ask": True, "terms": True}, {"type": "recap"},
                            wait_for=None)
    assert session.run() == 0
    assert [e["code"] for e in events(out) if e["type"] == "error"] == ["bad_command"] * 3


def test_a_call_that_stops_hearing_me_says_so_in_the_transcript_and_the_summary(tmp_path):
    hearing_off = {"type": "set_hearing", "on": False}
    sections = {"type": "set_sections", "ask": False, "terms": True}
    session, out, fake, _ = make(tmp_path, START, sections, hearing_off, {"type": "recap"}, then=SUMMARIZE)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    assert "מכאן CallCoach לא שומע אותי" in (call / "transcript.md").read_text(encoding="utf-8")
    log = (call / "events.log").read_text(encoding="utf-8")
    assert '"kind": "hearing"' in log and '"kind": "sections"' in log
    [summarized] = [m for kind, m in fake.calls if kind == "summarize"]
    assert "# When CallCoach did not hear me (I may have spoken then)" in summarized
    [recap] = [e for e in events(out) if e["type"] == "recap"]
    assert recap["text"] and isinstance(recap["ok"], bool)


def write_brief(tmp_path, terms):
    folder = tmp_path / "data" / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    rows = "".join(f"  - {t}\n" for t in terms)
    (folder / "2026-11-17-acme.md").write_text(f"---\ntitle: שיחה עם Acme\nterms:\n{rows}---\n## רקע\nAcme.\n",
                                               encoding="utf-8")


def test_the_terms_of_the_brief_show_as_soon_as_they_are_said(tmp_path):
    write_brief(tmp_path, ["Everyone: כל מי שנמצא בשיחה", "okta: כבר במילון"])
    servers = Servers()
    session, out, fake, _ = make(tmp_path, {**START, "brief": "2026-11-17-acme.md"}, servers=servers)
    assert session.run() == 0
    [term] = [e for e in events(out) if e["type"] == "term" and e["term"] == "Everyone"]
    assert (term["note"], term["kind"], term["lang"], term["known"], term["ours"]) == \
        ("כל מי שנמצא בשיחה", "term", "he", False, [])
    if GLOSSARY_PROMPT:
        assert servers.glossary == ("Okta", "Everyone")
    log = (next((tmp_path / "data" / "calls").iterdir()) / "events.log").read_text(encoding="utf-8")
    assert '"brief_terms": 2' in log and '"kind": "partial"' in log


def test_the_glossary_gets_the_names_of_the_brief_terms_once():
    from callcoach.session import with_terms

    assert with_terms(("Okta",), [("okta", "x"), ("SOC 2", "y"), ("soc 2", "z")]) == ("Okta", "SOC 2")
    assert with_terms((), ()) == ()


def test_goals_set_by_hand_reach_meta_json(tmp_path):
    session, out, _, _ = make(tmp_path, START, {"type": "set_goal", "id": "g1", "done": True},
                              {"type": "focus_goal", "id": None})
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    meta = json.loads((call / "meta.json").read_text(encoding="utf-8"))
    assert meta["goals"][0]["done"] is True


def test_a_start_while_warming_records_at_once_and_transcribes_after(tmp_path):
    session, out, _, servers = make(tmp_path, START, servers=SlowServers())
    assert session.run() == 0
    es, ts = events(out), types(out)
    active = [e for e in es if e["type"] == "status" and e["state"] == "active"]
    assert active and active[0]["transcribing"] is False
    assert ts.index("ready") < ts.index("line")
    after_ready = es[ts.index("ready"):]
    assert not [e for e in after_ready if e["type"] == "status" and e["state"] == "idle"]
    assert next(e for e in es if e["type"] == "finalized")["seconds"] > 0 and servers.started


def test_a_warm_up_that_fails_during_the_call_reports_one_error(tmp_path):
    session, out, _, _ = make(tmp_path, START, servers=FailingServers())
    assert session.run() == 0
    es = events(out)
    assert [e["code"] for e in es if e["type"] == "error"] == ["transcription_start_failed"]
    assert next(e for e in es if e["type"] == "finalized")["ok"] and types(out)[-1] == "stopped"
    assert "line" not in types(out)


def test_a_command_that_fails_in_the_call_is_reported_and_the_next_command_still_runs(tmp_path, monkeypatch):
    from callcoach import session as session_module
    real = session_module.CallAgent.command

    def command(self, engine, cmd):
        if cmd["type"] == "add_context" and cmd["text"] == "boom":
            raise RuntimeError("broken command")
        return real(self, engine, cmd)

    monkeypatch.setattr(session_module.CallAgent, "command", command)
    session, out, fake, _ = make(tmp_path, START, {"type": "add_context", "text": "boom"},
                                 {"type": "add_context", "text": "דנה מנהלת IT ב-Acme"}, then=SUMMARIZE)
    assert session.run() == 0
    errors = [e for e in events(out) if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["command_failed"] and "broken command" in errors[0]["message"]
    summary = [m for kind, m in fake.calls if kind == "summarize"]
    assert summary and "דנה מנהלת IT ב-Acme" in summary[0]


def test_the_end_of_a_call_copies_it_to_the_export_folder(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    session, out, _, _ = make(tmp_path, START, environ={"CALLCOACH_EXPORT": str(root)}, then=SUMMARIZE)
    assert session.run() == 0
    call = next((tmp_path / "data" / "calls").iterdir())
    copied = root / call.name
    assert sorted(p.name for p in copied.iterdir()) == ["summary.md", "transcript.md"]
    assert (copied / "summary.md").read_text(encoding="utf-8") == (call / "summary.md").read_text(encoding="utf-8")
    assert json.loads((call / "meta.json").read_text(encoding="utf-8"))["export_error"] is None
    assert "export_failed" not in [e.get("code") for e in events(out)]


def test_an_export_folder_that_is_gone_is_reported_and_the_call_still_ends(tmp_path):
    session, out, _, _ = make(tmp_path, START, environ={"CALLCOACH_EXPORT": str(tmp_path / "gone")})
    assert session.run() == 0
    es = events(out)
    assert [e["code"] for e in es if e["type"] == "error"] == ["export_failed"]
    assert [e["type"] for e in es][-2:] == ["summary_ready", "stopped"] and not (tmp_path / "gone").exists()


def test_the_status_carries_the_live_usage_cpu_and_limit(tmp_path):
    session, out, _, _ = make(tmp_path, START)
    assert session.run() == 0
    statuses = [e for e in events(out) if e["type"] == "status" and e["state"] == "active"]
    assert statuses and all({"tokens", "features", "cpu", "limit"} <= e.keys() for e in statuses)
    assert statuses[-1]["limit"] == "ok" and statuses[-1]["tokens"] >= statuses[0]["tokens"]
