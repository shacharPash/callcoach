import threading
import time
import urllib.error

import pytest

from callcoach.transcriber import DownTranscriber, ServerTranscriber, TranscriptionDown, WarmingTranscriber


class FakeProc:
    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


class FakeServer:
    def __init__(self, alive=True):
        self.proc = FakeProc(alive)
        self.restarts = 0
        self.url = "http://fake"

    def start(self):
        pass

    def wait_ready(self):
        pass

    def stop(self):
        pass

    def restart(self):
        self.restarts += 1
        self.proc = FakeProc(True)


class ScriptedClient:
    plan: list = []
    prompts: list = []

    def __init__(self, url):
        pass

    def transcribe(self, pcm, lang, prompt=""):
        ScriptedClient.prompts.append(prompt)
        result = ScriptedClient.plan.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def detect(self, pcm):
        return {"he": 0.9}


def make(alive=True, langid=True):
    servers = {"he": FakeServer(alive)}
    if langid:
        servers["langid"] = FakeServer()
    t = ServerTranscriber(servers, client_factory=ScriptedClient)
    t.start()
    return t, servers["he"]


def test_a_dead_server_is_restarted_once():
    t, he = make(alive=False)
    ScriptedClient.plan = [urllib.error.URLError("refused"), "ok"]
    assert t.transcribe(b"", "he") == "ok"
    assert he.restarts == 1


def test_an_error_from_a_live_server_is_not_a_crash():
    t, he = make(alive=True)
    ScriptedClient.plan = [urllib.error.URLError("timeout")]
    with pytest.raises(urllib.error.URLError):
        t.transcribe(b"", "he")
    assert he.restarts == 0


def test_a_second_crash_stops_transcription():
    t, he = make(alive=False)
    ScriptedClient.plan = [urllib.error.URLError("refused"), "ok"]
    t.transcribe(b"", "he")
    he.proc = FakeProc(False)
    ScriptedClient.plan = [urllib.error.URLError("refused")]
    with pytest.raises(TranscriptionDown):
        t.transcribe(b"", "he")


def test_detect_is_skipped_without_a_langid_server():
    t, _ = make(langid=False)
    assert t.detect(b"") is None


def test_a_call_that_failed_during_a_restart_is_retried():
    t, he = make(alive=True)

    class Swapping(ScriptedClient):
        def transcribe(self, pcm, lang, prompt=""):
            t.clients["he"] = ScriptedClient("http://fake")
            raise urllib.error.URLError("restarting")

    t.clients["he"] = Swapping("http://fake")
    ScriptedClient.plan = ["ok"]
    assert t.transcribe(b"", "he") == "ok"
    assert he.restarts == 0


def test_a_server_that_dies_again_on_the_retry_stops_transcription():
    t, he = make(alive=False)

    def restart_still_dead():
        he.restarts += 1

    he.restart = restart_still_dead
    ScriptedClient.plan = [urllib.error.URLError("refused"), urllib.error.URLError("refused")]
    with pytest.raises(TranscriptionDown):
        t.transcribe(b"", "he")
    assert he.restarts == 1


def test_a_restart_that_fails_stops_transcription():
    t, he = make(alive=False)

    def broken():
        raise RuntimeError("model failed to load")

    he.restart = broken
    ScriptedClient.plan = [urllib.error.URLError("refused")]
    with pytest.raises(TranscriptionDown):
        t.transcribe(b"", "he")


class DeadDetect(ScriptedClient):
    def detect(self, pcm):
        raise urllib.error.URLError("refused")


def test_language_detection_that_dies_twice_only_stops_detection():
    errors = []
    servers = {"he": FakeServer(), "langid": FakeServer(alive=False)}
    servers["langid"].restart = lambda: None
    t = ServerTranscriber(servers, client_factory=DeadDetect, on_error=errors.append)
    t.start()
    assert t.detect(b"") is None
    assert t.detect(b"") is None
    assert len(errors) == 1
    ScriptedClient.plan = ["ok"]
    assert t.transcribe(b"", "he") == "ok"


def test_the_glossary_becomes_the_prompt():
    t, _ = make()
    ScriptedClient.plan = ["x", "y"]
    ScriptedClient.prompts = []
    t.set_glossary(["SOC 2", " Okta ", ""])
    t.transcribe(b"", "he")
    t.set_glossary([])
    t.transcribe(b"", "he")
    assert ScriptedClient.prompts == ["SOC 2, Okta", ""]


def test_a_long_glossary_is_cut():
    t, _ = make()
    t.set_glossary([f"term{i}" for i in range(100)])
    assert len(t.prompt) <= 224


def test_the_servers_report_their_notes(tmp_path, monkeypatch):
    import callcoach.transcriber as tr

    monkeypatch.setattr(tr, "tool", lambda name: "/x/whisper-server")
    notes = []
    on_note = lambda kind, **fields: notes.append(kind)
    servers = tr.default_servers(tmp_path, on_note=on_note)
    assert set(servers) == {"he", "en", "langid"} and all(s.on_note is on_note for s in servers.values())


class Inner:
    down = False
    glossary = ()
    stopped = False

    def set_glossary(self, terms):
        self.glossary = tuple(terms)

    def transcribe(self, pcm, lang):
        return "שלום"

    def detect(self, pcm):
        return {"he": 1.0}

    def stop(self):
        self.stopped = True


def test_a_warming_transcriber_holds_requests_until_the_servers_are_ready():
    warm = WarmingTranscriber()
    warm.set_glossary(["Okta"])
    got = []
    worker = threading.Thread(target=lambda: got.append(warm.transcribe(b"\0" * 320, "he")))
    worker.start()
    time.sleep(0.1)
    assert got == [] and not warm.ready and not warm.down
    inner = Inner()
    warm.set(inner)
    worker.join(2)
    assert got == ["שלום"] and warm.ready and inner.glossary == ("Okta",)
    assert warm.detect(b"\0" * 320) == {"he": 1.0}
    warm.stop()
    assert inner.stopped


def test_servers_that_never_started_make_it_down_after_it_is_set():
    warm = WarmingTranscriber()
    warm.set(DownTranscriber("no servers"))
    assert warm.down
    with pytest.raises(TranscriptionDown):
        warm.transcribe(b"\0" * 320, "he")
