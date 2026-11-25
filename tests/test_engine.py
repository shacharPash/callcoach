import io
import json
import threading
import time

import numpy as np
import pytest
from audio_helpers import (RATE, delayed, has_ffmpeg, loud, peak, quiet, speech_like, to_pcm, tone,
                           write_stereo)

from callcoach.data import ME, THEM, Chunk
from callcoach.engine import CallEngine
from callcoach.protocol import Emitter
from callcoach.recorder import needs_recovery
from callcoach.sources import FileSource
from callcoach.transcriber import DownTranscriber, TranscriptionDown

pytestmark = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")

TEXT = {4000: "שלום לכולם", 8000: "מה שלומכם היום", 12000: "hello everyone"}


class FakeTranscriber:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def detect(self, pcm):
        return {"en": 0.95, "he": 0.03} if peak(pcm) == 12000 else {"he": 0.9, "en": 0.05}

    def transcribe(self, pcm, lang):
        if self.fail:
            raise self.fail
        self.calls.append((peak(pcm), lang))
        return TEXT[peak(pcm)]


def make_engine(tmp_path, left, right, transcriber, realtime=False, **kw):
    wav = tmp_path / "in.wav"
    write_stereo(wav, left, right)
    out = io.StringIO()
    kw.setdefault("new_vad", lambda: loud)
    engine = CallEngine(folder=tmp_path / "call", sources=[FileSource(wav, realtime=realtime)],
                        transcriber=transcriber, emitter=Emitter(out), start_lang="he", **kw)
    return engine, out


def events_of(out):
    return [json.loads(l) for l in out.getvalue().splitlines()]


def lines_of(events):
    return [e for e in events if e["type"] == "line"]


ME_AUDIO = quiet(0.5) + tone(1.0, 4000) + quiet(6.0)
THEM_AUDIO = quiet(2.5) + tone(2.0, 8000) + quiet(1.0) + tone(2.0, 12000) + quiet(1.5)


def test_a_call_is_transcribed_per_channel_and_language(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())
    result = engine.run()
    events = events_of(out)
    assert result.ok
    got = sorted((l["channel"], l["lang"], l["text"]) for l in lines_of(events))
    assert got == sorted([("me", "he", "שלום לכולם"), ("them", "he", "מה שלומכם היום"),
                          ("them", "en", "hello everyone")])
    assert next(e for e in events if e["type"] == "ready")["replay_t0"] is not None
    assert events[-1]["type"] == "stopped"
    folder = tmp_path / "call"
    assert (folder / "call.m4a").exists() and (folder / "transcript.md").exists()
    assert len((folder / "lines.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_speaker_echo_on_the_mic_is_dropped(tmp_path):
    same = quiet(1.0) + tone(2.0, 8000) + quiet(2.0)
    engine, out = make_engine(tmp_path, same, same, FakeTranscriber())
    engine.run()
    assert [(l["channel"], l["text"]) for l in lines_of(events_of(out))] == [("them", "מה שלומכם היום")]


def test_transcription_errors_do_not_stop_the_recording(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber(fail=RuntimeError("boom")))
    result = engine.run()
    events = events_of(out)
    assert result.ok and lines_of(events) == []
    assert len([e for e in events if e["type"] == "error" and e["code"] == "transcription_failed"]) == 3


def test_transcription_down_is_reported_once(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber(fail=TranscriptionDown("x")))
    result = engine.run()
    events = events_of(out)
    assert result.ok
    assert len([e for e in events if e["type"] == "error" and e["code"] == "transcription_stopped"]) == 1


def test_locked_language_is_used_for_everything(tmp_path):
    fake = FakeTranscriber()
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, fake)
    engine.lock_language("en")
    engine.run()
    assert {lang for _, lang in fake.calls} == {"en"}


def test_an_unlocked_language_is_detected_again(tmp_path):
    fake = FakeTranscriber()
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, fake)
    engine.lock_language("en")
    engine.unlock_language()
    engine.run()
    assert {lang for _, lang in fake.calls} == {"he", "en"}


def test_mark_is_stored(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())
    engine.mark()
    engine.run()
    assert "## Marked moments" in (tmp_path / "call" / "transcript.md").read_text(encoding="utf-8")


def test_auto_stop_after_silence(tmp_path):
    engine, out = make_engine(tmp_path, quiet(5.0), quiet(5.0), FakeTranscriber(), realtime=True,
                              auto_stop_seconds=1.0)
    started = time.monotonic()
    engine.run()
    assert time.monotonic() - started < 3.0
    assert any(e["type"] == "error" and e["code"] == "auto_stop" for e in events_of(out))


def test_stop_request_ends_the_call(tmp_path):
    engine, out = make_engine(tmp_path, quiet(10.0), quiet(10.0), FakeTranscriber(), realtime=True)
    threading.Timer(0.5, engine.request_stop).start()
    started = time.monotonic()
    result = engine.run()
    assert time.monotonic() - started < 3.0 and result.ok


def test_a_full_disk_stops_the_recording_but_not_the_transcript(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())

    def full(chunk):
        raise OSError(28, "No space left on device")

    engine.recorder.write = full
    engine.run()
    events = events_of(out)
    assert len(lines_of(events)) == 3
    assert len([e for e in events if e["type"] == "error" and e["code"] == "recording_stopped"]) == 1
    assert events[-1]["type"] == "stopped"


def test_a_full_disk_for_the_transcript_file_keeps_the_call_going(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())

    def full(*args):
        raise OSError(28, "No space left on device")

    engine.store.add = engine.store.bookmark = engine.store.render = full
    engine.mark()
    result = engine.run()
    events = events_of(out)
    assert result.ok and len(lines_of(events)) == 3
    assert len([e for e in events if e["type"] == "error" and e["code"] == "transcript_stopped"]) == 1
    assert events[-1]["type"] == "stopped"


class BrokenSource:
    def start(self, sink):
        raise RuntimeError("process tap not permitted")

    def stop(self):
        pass


def test_a_source_that_cannot_start_does_not_lose_the_others(tmp_path):
    wav = tmp_path / "in.wav"
    write_stereo(wav, ME_AUDIO, THEM_AUDIO)
    out = io.StringIO()
    engine = CallEngine(folder=tmp_path / "call", sources=[FileSource(wav, realtime=False), BrokenSource()],
                        transcriber=FakeTranscriber(), emitter=Emitter(out), new_vad=lambda: loud, start_lang="he")
    result = engine.run()
    events = events_of(out)
    assert result.ok and len(lines_of(events)) == 3
    assert any(e["type"] == "error" and e["code"] == "source" for e in events)
    assert (tmp_path / "call" / "call.m4a").exists()


def test_the_last_words_of_the_call_are_kept(tmp_path):
    engine, out = make_engine(tmp_path, quiet(3.0) + tone(1.0, 4000), quiet(3.0) + tone(1.0, 8000),
                              FakeTranscriber())
    engine.run()
    assert sorted(l["text"] for l in lines_of(events_of(out))) == sorted(["שלום לכולם", "מה שלומכם היום"])
    assert len((tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_regular_speech_does_not_auto_stop(tmp_path):
    me = (tone(0.4, 4000) + quiet(0.8)) * 4
    engine, out = make_engine(tmp_path, me, quiet(4.8), FakeTranscriber(), realtime=True, auto_stop_seconds=2.5)
    engine.run()
    events = events_of(out)
    assert not any(e["type"] == "error" and e["code"] == "auto_stop" for e in events)
    assert len(lines_of(events)) == 4


class SlowThem(FakeTranscriber):
    def transcribe(self, pcm, lang):
        time.sleep(0.5 if peak(pcm) == 8000 else 0.15)
        return super().transcribe(pcm, lang)


def test_line_ready_is_when_the_line_is_published(tmp_path):
    engine, out = make_engine(tmp_path, quiet(1.0) + tone(1.0, 4000) + quiet(2.0),
                              quiet(1.2) + tone(1.0, 8000) + quiet(1.8), SlowThem())
    engine.run()
    lines = lines_of(events_of(out))
    assert [l["channel"] for l in lines] == ["them", "me"]
    assert all(abs(l["line_ready"] - l["t"]) < 0.1 for l in lines)


def test_a_call_with_no_audio_leaves_nothing_to_recover(tmp_path):
    engine = CallEngine(folder=tmp_path / "call", sources=[], transcriber=FakeTranscriber(),
                        emitter=Emitter(io.StringIO()), new_vad=lambda: loud, start_lang="he", auto_stop_seconds=1.0)
    result = engine.run()
    assert not result.ok and result.reason == "no audio was recorded"
    assert not needs_recovery(tmp_path / "call")
    assert not list((tmp_path / "call").glob("*.wav"))


class HangoverVad:
    def __init__(self):
        self.left = 0

    def __call__(self, frame):
        if loud(frame):
            self.left = 20
            return True
        self.left = max(self.left - 1, 0)
        return self.left > 0


def test_each_channel_has_its_own_speech_detector(tmp_path):
    engine, out = make_engine(tmp_path, quiet(4.0), quiet(1.0) + tone(2.0, 8000) + quiet(1.0),
                              FakeTranscriber(), new_vad=HangoverVad)
    engine.run()
    segments = [e for e in events_of(out) if e["type"] == "segment"]
    assert [s["channel"] for s in segments] == ["them"]


class FailingLangid(FakeTranscriber):
    def detect(self, pcm):
        raise RuntimeError("HTTP Error 500: Internal Server Error")


def test_a_language_id_error_keeps_the_line(tmp_path):
    engine, out = make_engine(tmp_path, quiet(5.0), quiet(0.5) + tone(2.0, 8000) + quiet(2.5), FailingLangid())
    engine.run()
    events = events_of(out)
    assert [l["text"] for l in lines_of(events)] == ["מה שלומכם היום"]
    assert any(e["type"] == "error" and e["code"] == "langid_failed" for e in events)


def test_a_finished_call_leaves_nothing_to_recover(tmp_path):
    engine, _ = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())
    assert engine.run().ok
    assert not (tmp_path / "call" / ".recording").exists()
    assert not needs_recovery(tmp_path / "call")


class OddChunks:
    def __init__(self):
        self.done = threading.Event()

    def start(self, sink):
        sink(Chunk(ME, b"\x00" * 3, time.time()))
        sink(Chunk(ME, b"\x00" * 3, time.time()))
        self.done.set()

    def stop(self):
        pass


def test_a_bad_chunk_still_finalizes_the_call(tmp_path):
    out = io.StringIO()
    engine = CallEngine(folder=tmp_path / "call", sources=[OddChunks()], transcriber=FakeTranscriber(),
                        emitter=Emitter(out), new_vad=lambda: loud, start_lang="he")
    engine.run()
    types = [e["type"] for e in events_of(out)]
    assert "finalized" in types and types[-1] == "stopped"


class Broken:
    done = None

    def start(self, sink):
        raise RuntimeError("no device")

    def stop(self):
        pass


def test_no_source_ends_the_call_with_a_clear_error(tmp_path):
    out = io.StringIO()
    engine = CallEngine(folder=tmp_path / "call", sources=[Broken()], transcriber=FakeTranscriber(),
                        emitter=Emitter(out), new_vad=lambda: loud, start_lang="he")
    engine.run()
    events = events_of(out)
    codes = [e["code"] for e in events if e["type"] == "error"]
    assert "no_sources" in codes and events[-1]["type"] == "stopped"


def test_a_mark_after_the_call_is_ignored(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())
    engine.run()
    engine.mark()
    text = (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8")
    assert '"bookmark"' not in text


class CountingTranscriber:
    def __init__(self):
        self.transcribed = 0
        self.detected = 0

    def detect(self, pcm):
        self.detected += 1
        return {"en": 0.95, "he": 0.03}

    def transcribe(self, pcm, lang):
        self.transcribed += 1
        return "the other side talking"


def echo_call():
    them = np.concatenate([np.zeros(RATE), speech_like(3, 1), np.zeros(2 * RATE)])
    mic = delayed(them, 80, 0.2)
    return to_pcm(mic), to_pcm(them)


def test_echo_of_the_other_side_on_the_mic_is_never_transcribed(tmp_path):
    me, them = echo_call()
    fake = CountingTranscriber()
    engine, out = make_engine(tmp_path, me, them, fake)
    engine.run()
    lines = lines_of(events_of(out))
    assert [l["channel"] for l in lines] == ["them"]
    assert fake.transcribed == 1 and fake.detected == 1
    assert engine.routers["me"].current == "he"


def test_the_audio_check_is_off_while_the_echo_cancelled_helper_runs(tmp_path):
    me, them = echo_call()
    fake = CountingTranscriber()
    engine, out = make_engine(tmp_path, me, them, fake, echo_check=lambda: False)
    engine.run()
    assert fake.transcribed == 2


def test_the_audio_echo_check_can_be_turned_off(tmp_path):
    me, them = echo_call()
    fake = CountingTranscriber()
    engine, out = make_engine(tmp_path, me, them, fake, echo_threshold=None)
    engine.run()
    assert fake.transcribed == 2


def leftover_call(mine_db):
    """The other side talks for 3 s; the mic meanwhile has independent audio whose loud part is near mine_db."""
    them = np.concatenate([np.zeros(RATE), speech_like(3, 1), np.zeros(2 * RATE)])
    x = speech_like(3, 7, level=0.05)
    x = x * 10 ** ((mine_db - 20 * np.log10(np.percentile(np.abs(x[x != 0]), 90) + 1e-12)) / 20)
    mic = np.concatenate([np.zeros(RATE), x, np.zeros(2 * RATE)])
    return to_pcm(mic), to_pcm(them)


def faint(frame):
    return peak(frame) > 30


class ByLevel(CountingTranscriber):
    def transcribe(self, pcm, lang):
        super().transcribe(pcm, lang)
        return "the other side talking" if peak(pcm) > 20000 else "something on my side"


def test_a_faint_leftover_of_the_echo_over_the_other_side_is_dropped(tmp_path):
    me, them = leftover_call(-58)
    fake = ByLevel()
    log = tmp_path / "events.log"
    engine, out = make_engine(tmp_path, me, them, fake, new_vad=lambda: faint)
    engine.emitter.set_log(log)
    engine.run()
    assert [l["channel"] for l in lines_of(events_of(out))] == ["them"]
    notes = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    dropped = [n for n in notes if n.get("kind") == "echo_dropped"]
    assert dropped and all("level" in n and n["limit"] == -50.0 for n in dropped)


def test_my_voice_over_the_other_side_is_kept(tmp_path):
    me, them = leftover_call(-25)
    fake = ByLevel()
    engine, out = make_engine(tmp_path, me, them, fake, new_vad=lambda: faint)
    engine.run()
    assert sorted(l["channel"] for l in lines_of(events_of(out)))[0] == "me"


def test_the_level_gate_can_be_turned_off(tmp_path):
    me, them = leftover_call(-58)
    fake = ByLevel()
    engine, out = make_engine(tmp_path, me, them, fake, new_vad=lambda: faint, echo_gate_db=None)
    engine.run()
    assert "me" in [l["channel"] for l in lines_of(events_of(out))]


class SilentOnEnglish(FakeTranscriber):
    def transcribe(self, pcm, lang):
        return "" if peak(pcm) == 12000 else super().transcribe(pcm, lang)


def test_a_segment_without_text_does_not_switch_the_language(tmp_path):
    me = quiet(0.5) + tone(2.0, 12000) + quiet(3.0)
    engine, out = make_engine(tmp_path, me, quiet(5.5), SilentOnEnglish())
    engine.run()
    assert engine.routers["me"].current == "he"


def test_a_text_echo_does_not_switch_the_mic_language(tmp_path):
    same = quiet(1.0) + tone(2.0, 12000) + quiet(2.0)
    engine, out = make_engine(tmp_path, same, same, FakeTranscriber(), echo_threshold=None)
    engine.run()
    assert [l["channel"] for l in lines_of(events_of(out))] == ["them"]
    assert engine.routers["me"].current == "he" and engine.routers["them"].current == "en"


class Checked(FileSource):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.checks = 0

    def check(self, now):
        self.checks += 1


def test_the_engine_checks_its_sources_every_status_tick(tmp_path):
    wav = tmp_path / "in.wav"
    write_stereo(wav, ME_AUDIO, THEM_AUDIO)
    source = Checked(wav, realtime=True)
    engine = CallEngine(folder=tmp_path / "call", sources=[source], transcriber=FakeTranscriber(),
                        emitter=Emitter(io.StringIO()), new_vad=lambda: loud, start_lang="he", status_every=0.5)
    engine.run()
    assert source.checks >= 10


def test_hooks_see_segments_lines_and_the_end_in_order(tmp_path):
    seen = []
    engine, out = make_engine(
        tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber(),
        on_segment=lambda seg: seen.append(("segment", seg.channel)),
        on_line=lambda line, lang: seen.append(("line", line.text, lang)),
        on_stopping=lambda: seen.append(("stopping", [e["type"] for e in events_of(out)][-1])),
        on_finished=lambda result: seen.append(("finished", result.ok, [e["type"] for e in events_of(out)][-1])))
    engine.run()
    kinds = [s[0] for s in seen]
    assert kinds.count("segment") == 3 and kinds.count("line") == 3
    assert ("line", "שלום לכולם", "he") in seen
    assert seen[-1][:2] == ("finished", True) and seen[-1][2] != "stopped"
    stopping = next(s for s in seen if s[0] == "stopping")
    assert stopping[1] == "status"
    types = [e["type"] for e in events_of(out)]
    states = [e["state"] for e in events_of(out) if e["type"] == "status"]
    assert states[-1] == "stopping" and types.index("finalized") > len(types) - 1 - types[::-1].index("status")
    assert types[-1] == "stopped"


def test_a_failing_hook_never_stops_the_call(tmp_path):
    def boom(*args):
        raise RuntimeError("hook bug")

    log = tmp_path / "events.log"
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber(), on_line=boom, on_finished=boom)
    engine.emitter.set_log(log)
    engine.run()
    events = events_of(out)
    assert len(lines_of(events)) == 3 and events[-1]["type"] == "stopped"
    assert "hook_failed" in log.read_text(encoding="utf-8")


def test_a_down_transcriber_records_without_lines(tmp_path):
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, DownTranscriber("whisper-server not found"))
    result = engine.run()
    events = events_of(out)
    assert result.ok and not lines_of(events)
    assert not [e for e in events if e["type"] == "error"]


def test_a_failing_echo_check_still_transcribes(tmp_path, monkeypatch):
    import callcoach.engine as engine_mod

    def broken(mic, them):
        raise ValueError("bad audio")

    monkeypatch.setattr(engine_mod, "echo_score", broken)
    log = tmp_path / "events.log"
    engine, out = make_engine(tmp_path, ME_AUDIO, THEM_AUDIO, FakeTranscriber())
    engine.emitter.set_log(log)
    engine.run()
    assert len(lines_of(events_of(out))) == 3
    notes = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [n["error"] for n in notes if n.get("kind") == "echo_check_failed"] == ["ValueError: bad audio"]


class BracketsInEnglish(FakeTranscriber):
    def detect(self, pcm):
        return {"en": 0.95, "he": 0.03}

    def transcribe(self, pcm, lang):
        self.calls.append((peak(pcm), lang))
        return "[foreign language]" if lang == "en" else "שלום לכולם"


def test_a_switch_that_brings_no_text_retries_in_the_current_language(tmp_path):
    me = quiet(0.5) + tone(2.0, 4000) + quiet(3.0)
    fake = BracketsInEnglish()
    engine, out = make_engine(tmp_path, me, quiet(5.5), fake)
    engine.run()
    lines = lines_of(events_of(out))
    assert [(l["text"], l["lang"]) for l in lines] == [("שלום לכולם", "he")]
    assert fake.calls == [(4000, "en"), (4000, "he")]
    assert engine.routers["me"].current == "he"


LONG_THEM = quiet(0.5) + tone(5.0, 8000) + quiet(1.5)


def test_a_long_sentence_of_the_other_side_is_read_while_it_is_said(tmp_path):
    seen = []
    engine, out = make_engine(tmp_path, quiet(0.5) + tone(5.0, 4000) + quiet(1.5), LONG_THEM, FakeTranscriber(),
                              on_partial=lambda channel, text: seen.append((channel, text)))
    engine.run()
    events = events_of(out)
    assert seen and set(seen) == {("them", "מה שלומכם היום")}
    [line] = [l for l in lines_of(events) if l["channel"] == "them"]
    assert line["text"] == "מה שלומכם היום"
    partials = [e for e in events if e["type"] == "partial"]
    assert len(partials) == len(seen)
    assert {(p["channel"], p["text"], p["t_start"]) for p in partials} == {("them", line["text"], line["t_start"])}


def test_no_partial_reaches_the_app_without_a_partial_hook(tmp_path):
    transcriber = FakeTranscriber()
    engine, out = make_engine(tmp_path, quiet(7.0), LONG_THEM, transcriber)
    engine.run()
    assert not any(e["type"] == "partial" for e in events_of(out)) and transcriber.calls == [(8000, "he")]


def test_a_short_sentence_gets_no_partial(tmp_path):
    seen = []
    engine, _ = make_engine(tmp_path, quiet(4.0), quiet(0.5) + tone(1.5, 8000) + quiet(1.5), FakeTranscriber(),
                            on_partial=lambda channel, text: seen.append(text))
    engine.run()
    assert seen == []


def test_no_partials_when_they_are_not_wanted(tmp_path):
    transcriber, seen = FakeTranscriber(), []
    engine, _ = make_engine(tmp_path, quiet(7.0), LONG_THEM, transcriber,
                            on_partial=lambda channel, text: seen.append(text), partials_wanted=lambda: False)
    engine.run()
    assert seen == [] and transcriber.calls == [(8000, "he")]


def test_a_failed_partial_is_logged_and_the_call_goes_on(tmp_path):
    log = tmp_path / "events.log"
    engine, out = make_engine(tmp_path, quiet(7.0), LONG_THEM, FakeTranscriber(fail=RuntimeError("boom")),
                              on_partial=lambda channel, text: None)
    engine.emitter.set_log(log)
    assert engine.run().ok
    assert '"partial_failed"' in log.read_text(encoding="utf-8")


class Inline:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, wait=True):
        pass


class Pushed:
    """Pushes all of the other side's audio, in 20 ms chunks, as soon as it starts."""

    def __init__(self, them, t0=None):
        self.them = them
        self.t0 = time.time() if t0 is None else t0
        self.done = threading.Event()

    def start(self, sink):
        step = RATE * 2 // 50
        for i in range(0, len(self.them), step):
            sink(Chunk(THEM, self.them[i:i + step], self.t0 + (i + step) / (RATE * 2)))
        self.done.set()

    def stop(self):
        pass


def pushed_engine(tmp_path, them, transcriber, t0=None, **kw):
    out = io.StringIO()
    engine = CallEngine(folder=tmp_path / "call", sources=[Pushed(them, t0)], transcriber=transcriber,
                        emitter=Emitter(out), new_vad=lambda: loud, start_lang="he", **kw)
    engine.pool = Inline()
    return engine, out


def test_each_long_sentence_gets_its_own_partial(tmp_path):
    seen = []
    them = quiet(0.5) + tone(3.0, 8000) + quiet(1.0) + tone(3.0, 12000) + quiet(1.0)
    engine, out = pushed_engine(tmp_path, them, FakeTranscriber(), on_partial=lambda channel, text: seen.append(text))
    engine.run()
    events = events_of(out)
    assert seen == ["מה שלומכם היום", "hello everyone"]
    partials = [(e["text"], e["t_start"]) for e in events if e["type"] == "partial"]
    assert partials == [(l["text"], l["t_start"]) for l in lines_of(events)]


def test_no_partial_starts_once_the_call_is_stopping(tmp_path):
    seen = []
    engine, out = pushed_engine(tmp_path, LONG_THEM, FakeTranscriber(),
                                on_partial=lambda channel, text: seen.append(text))
    engine.request_stop()
    engine.run()
    assert seen == [] and [l["text"] for l in lines_of(events_of(out))] == ["מה שלומכם היום"]


def test_a_partial_that_finds_transcription_down_reports_it_once(tmp_path):
    log = tmp_path / "events.log"
    engine, out = pushed_engine(tmp_path, LONG_THEM, FakeTranscriber(fail=TranscriptionDown("could not restart: boom")),
                                on_partial=lambda channel, text: None)
    engine.emitter.set_log(log)
    engine.run()
    stopped = [e for e in events_of(out) if e["type"] == "error" and e["code"] == "transcription_stopped"]
    assert len(stopped) == 1 and "could not restart: boom" in stopped[0]["message"]
    assert '"partial_failed"' not in log.read_text(encoding="utf-8")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class SlowMac(FakeTranscriber):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def transcribe(self, pcm, lang):
        self.clock.t += 3.0
        return super().transcribe(pcm, lang)


def test_a_slow_partial_holds_the_next_one_for_as_long_as_it_took(tmp_path):
    clock, seen = Clock(), []
    engine, _ = pushed_engine(tmp_path, quiet(0.5) + tone(9.0, 8000) + quiet(1.0), SlowMac(clock), t0=clock.t,
                              clock=clock, on_partial=lambda channel, text: seen.append(text))
    engine.run()
    assert seen == ["מה שלומכם היום"]


CHUNK_BYTES = RATE * 20 // 1000 * 2


class Switching:
    """Both channels in 20 ms chunks on a clock of their own, with the hearing switch flipped at given seconds of the
    audio; each flip waits until the engine handled every chunk before it, so it lands exactly there."""

    t0 = 1000.0

    def __init__(self, me, them, switches):
        self.me, self.them = me, them
        self.switches = sorted(switches)
        self.done = threading.Event()
        self.engine = None
        self.sent = 0
        self.handled = 0
        self.now = self.t0

    def clock(self):
        return self.now

    def start(self, sink):
        threading.Thread(target=self._run, args=(sink,), daemon=True).start()

    def stop(self):
        pass

    def _run(self, sink):
        try:
            n = max(len(self.me), len(self.them)) // CHUNK_BYTES
            for i in range(n):
                while self.switches and self.switches[0][0] <= i * 0.02 + 1e-9:
                    self._settle()
                    _, on, *what = self.switches.pop(0)
                    (self.engine.set_paused if what == ["paused"] else self.engine.set_hearing)(on)
                self.now = self.t0 + (i + 1) * 0.02
                for channel, pcm in ((ME, self.me), (THEM, self.them)):
                    part = pcm[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
                    sink(Chunk(channel, part + bytes(CHUNK_BYTES - len(part)), self.now))
                    self.sent += 1
        finally:
            self.done.set()

    def _settle(self):
        deadline = time.monotonic() + 5
        while self.handled < self.sent and time.monotonic() < deadline:
            time.sleep(0.001)


def hearing_call(tmp_path, me, them, switches, **kw):
    """A call whose hearing switch flips at the given seconds; returns its events, the audio written per channel and
    the segments the on_segment hook saw."""
    source = Switching(me, them, switches)
    out, written, hooked = io.StringIO(), {ME: b"", THEM: b""}, []
    engine = CallEngine(folder=tmp_path / "call", sources=[source], transcriber=FakeTranscriber(),
                        emitter=Emitter(out), new_vad=lambda: loud, start_lang="he", echo_threshold=None,
                        on_segment=hooked.append, clock=source.clock, **kw)
    handle, write = engine._handle_chunk, engine.recorder.write

    def counted(chunk):
        handle(chunk)
        source.handled += 1

    def recorded(chunk):
        written[chunk.channel] += chunk.pcm
        write(chunk)

    engine._handle_chunk, engine.recorder.write = counted, recorded
    source.engine = engine
    result = engine.run()
    assert result.ok
    return engine, events_of(out), written, hooked


def samples(seconds):
    return round(seconds * RATE) * 2


TWO_SENTENCES = quiet(0.5) + tone(1.0, 4000) + quiet(1.0) + tone(1.0, 4000) + quiet(3.0)
THEM_LATE = quiet(4.0) + tone(1.0, 8000) + quiet(1.5)


def test_while_callcoach_does_not_hear_me_my_audio_is_silence_and_nothing_of_it_is_used(tmp_path):
    engine, events, written, hooked = hearing_call(tmp_path, TWO_SENTENCES, THEM_LATE, [(2.3, False)])
    assert [(l["channel"], l["text"]) for l in lines_of(events)] == [("me", "שלום לכולם"), ("them", "מה שלומכם היום")]
    assert [s["channel"] for s in events if s["type"] == "segment"] == ["me", "them"]
    assert peak(written[ME][:samples(2.3)]) == 4000 and peak(written[ME][samples(2.3):]) == 0
    assert len(written[ME]) == len(written[THEM])
    assert [s.channel for s in hooked] == ["me"]
    status = [e for e in events if e["type"] == "status"][-1]
    assert (status["hearing"], status["me_voice"], status["talk_share"]) == (False, False, 1.0)


def test_each_change_is_a_note_in_the_transcript(tmp_path):
    engine, events, *_ = hearing_call(tmp_path, TWO_SENTENCES, THEM_LATE, [(2.3, False), (4.6, True)])
    rows = [json.loads(l) for l in (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["on"], round(r["t"] - engine.started)) for r in rows if r["kind"] == "hearing"] == [(False, 2), (True, 5)]
    text = (tmp_path / "call" / "transcript.md").read_text(encoding="utf-8")
    assert text.index("שלום לכולם") < text.index("מכאן CallCoach לא שומע אותי") < text.index("מכאן CallCoach שומע אותי שוב")


def test_a_sentence_of_mine_still_open_when_hearing_goes_off_is_dropped(tmp_path):
    me = quiet(0.5) + tone(2.0, 4000) + quiet(4.0)
    engine, events, written, hooked = hearing_call(tmp_path, me, quiet(6.5), [(1.2, False)])
    assert lines_of(events) == [] and [e for e in events if e["type"] == "segment"] == [] and hooked == []
    assert peak(written[ME][samples(1.2):]) == 0


def test_hearing_back_mid_sentence_keeps_only_what_came_after(tmp_path):
    me = quiet(0.5) + tone(2.0, 4000) + quiet(4.0)
    engine, events, written, _ = hearing_call(tmp_path, me, quiet(6.5), [(1.0, False), (1.6, True)])
    [segment] = [e for e in events if e["type"] == "segment"]
    assert segment["t_start"] - Switching.t0 >= 1.55
    assert [l["text"] for l in lines_of(events)] == ["שלום לכולם"]
    assert peak(written[ME][samples(1.0):samples(1.6)]) == 0 and peak(written[ME][samples(1.6):]) == 4000


def test_while_the_recording_is_paused_both_sides_are_silence_and_nothing_is_used(tmp_path):
    engine, events, written, hooked = hearing_call(tmp_path, TWO_SENTENCES, THEM_LATE, [(2.3, True, "paused")])
    assert [l["channel"] for l in lines_of(events)] == ["me"]
    assert peak(written[ME][samples(2.3):]) == 0 and peak(written[THEM][samples(2.3):]) == 0
    assert len(written[ME]) == len(written[THEM])
    status = [e for e in events if e["type"] == "status"][-1]
    assert (status["paused"], status["hearing"], status["me_voice"]) == (True, True, False)
    rows = [json.loads(l) for l in (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["on"], round(r["t"] - engine.started)) for r in rows if r["kind"] == "paused"] == [(True, 2)]
    assert "מכאן ההקלטה מושהית" in (tmp_path / "call" / "transcript.md").read_text(encoding="utf-8")


def test_a_pause_drops_the_open_sentences_and_going_on_hears_both_sides_again(tmp_path):
    me = quiet(0.5) + tone(2.0, 4000) + quiet(4.0)
    them = quiet(4.0) + tone(1.0, 8000) + quiet(1.5)
    engine, events, written, _ = hearing_call(tmp_path, me, them, [(1.2, True, "paused"), (3.5, False, "paused")])
    assert [l["channel"] for l in lines_of(events)] == ["them"]
    assert peak(written[ME][samples(1.2):samples(3.5)]) == 0 and peak(written[THEM][samples(4.0):]) == 8000
    assert events[-1]["type"] == "stopped" and [e for e in events if e["type"] == "error"] == []
    assert "מכאן ההקלטה ממשיכה" in (tmp_path / "call" / "transcript.md").read_text(encoding="utf-8")


def test_a_paused_call_does_not_end_for_its_silence(tmp_path):
    _, paused, *_ = hearing_call(tmp_path / "a", quiet(4.0), quiet(4.0), [(0.1, True, "paused")], auto_stop_seconds=1.0)
    assert [e["code"] for e in paused if e["type"] == "error"] == []
    _, silent, *_ = hearing_call(tmp_path / "b", quiet(4.0), quiet(4.0), [], auto_stop_seconds=1.0)
    assert [e["code"] for e in silent if e["type"] == "error"] == ["auto_stop"]


def test_flipping_the_switch_many_times_mid_sentence_breaks_nothing(tmp_path):
    me = quiet(0.5) + tone(3.0, 4000) + quiet(3.0)
    flips = [(0.6 + i * 0.1, i % 2 == 1) for i in range(20)]
    engine, events, written, _ = hearing_call(tmp_path, me, THEM_LATE + quiet(1.0), flips)
    assert events[-1]["type"] == "stopped" and len(written[ME]) == len(written[THEM])
    assert [e for e in events if e["type"] == "error"] == []
    me_lines = [l for l in lines_of(events) if l["channel"] == "me"]
    assert [l["text"] for l in me_lines] == ["שלום לכולם"]
    rows = [json.loads(l) for l in (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["on"] for r in rows if r["kind"] == "hearing"] == [i % 2 == 1 for i in range(20)]


def test_the_same_switch_twice_changes_nothing(tmp_path):
    engine, events, *_ = hearing_call(tmp_path, TWO_SENTENCES, THEM_LATE, [(1.0, True), (2.0, False), (2.1, False)])
    rows = [json.loads(l) for l in (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["on"] for r in rows if r["kind"] == "hearing"] == [False]
    engine.set_hearing(True)
    rows = [json.loads(l) for l in (tmp_path / "call" / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["on"] for r in rows if r["kind"] == "hearing"] == [False]


def test_my_voice_shows_only_while_callcoach_hears_it(tmp_path):
    now = [100.0]
    engine = CallEngine(folder=tmp_path / "call", sources=[], transcriber=FakeTranscriber(),
                        emitter=Emitter(io.StringIO()), new_vad=lambda: loud, clock=lambda: now[0])
    assert engine._me_voice() is False
    engine.segmenters[ME].feed(tone(0.3, 4000), 100.0)
    assert engine._me_voice() is True
    now[0] = 101.5
    assert engine._me_voice() is False
    now[0] = 100.2
    engine.set_hearing(False)
    assert engine._me_voice() is False
