import io
import subprocess
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from audio_helpers import peak, tone, write_stereo

import callcoach.sources as sources
from callcoach.data import ME, THEM
from callcoach.sources import DropoutDetector, FileSource, MicSource, SystemSource


def play(path, **kw):
    got = []
    src = FileSource(path, **kw)
    src.start(got.append)
    assert src.done.wait(5)
    return src, got


def test_file_source_splits_the_channels(tmp_path):
    p = tmp_path / "s.wav"
    write_stereo(p, tone(1.0, 1000), tone(1.0, 2000))
    _, got = play(p, realtime=False, clock=lambda: 50.0)
    me = [c for c in got if c.channel == ME]
    them = [c for c in got if c.channel == THEM]
    assert sum(len(c.pcm) for c in me) == sum(len(c.pcm) for c in them) == 32000
    assert peak(me[0].pcm) == 1000 and peak(them[0].pcm) == 2000
    assert them[-1].t_end == pytest.approx(51.0)
    assert all(a.t_end < b.t_end for a, b in zip(them, them[1:]))


def test_realtime_playback_waits_for_each_chunk(tmp_path):
    p = tmp_path / "s.wav"
    write_stereo(p, tone(1.0), tone(1.0))
    now = [0.0]

    def sleep(d):
        now[0] += d

    play(p, realtime=True, clock=lambda: now[0], sleep=sleep)
    assert now[0] == pytest.approx(1.0, abs=0.03)


def test_wrong_format_is_reported(tmp_path):
    p = tmp_path / "bad.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(bytes(1600))
    src, _ = play(p, realtime=False)
    assert isinstance(src.error, ValueError)


def fmt(rate, channels=1, bits=32, kind="float"):
    return SimpleNamespace(sample_rate=rate, num_channels=channels, bits_per_sample=bits,
                           is_float=kind == "float", is_signed_integer=kind == "int")


def test_system_audio_is_resampled_to_16k_mono():
    s = SystemSource()
    x = (0.5 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype("<f4")
    stereo = np.repeat(x, 2).reshape(-1, 2)
    out = b"".join(s._convert(SimpleNamespace(data=part.astype("<f4").tobytes(), format=fmt(48000, 2)))
                   for part in np.array_split(stereo, 50))
    y = np.frombuffer(out, dtype="<i2")
    assert 15000 < len(y) <= 16000
    assert 14000 < np.abs(y).max() < 17500


def test_int16_system_audio_is_supported():
    s = SystemSource()
    x = (8000 * np.ones(4410)).astype("<i2")
    out = s._convert(SimpleNamespace(data=x.tobytes(), format=fmt(44100, 1, 16, "int")))
    assert len(out) > 0


def test_a_mic_that_cannot_start_is_reported():
    errors = []
    mic = MicSource(on_error=errors.append)

    def broken():
        raise FileNotFoundError("ffmpeg not found")

    mic._spawn = broken
    mic._run(lambda chunk: None)
    assert len(errors) == 1 and errors[0].startswith("microphone failed")


class FakeFfmpeg:
    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_the_mic_restarts_after_ffmpeg_exits_and_never_leaves_it_running():
    errors, got, procs = [], [], []
    mic = MicSource(on_error=errors.append)

    def spawn():
        if len(procs) == 2:
            mic._stop.set()
        procs.append(FakeFfmpeg(bytes(640)))
        return procs[-1]

    mic._spawn = spawn
    mic._stop.wait = lambda timeout: None
    mic._run(got.append)
    assert len(procs) == 3 and len(got) == 2 and len(errors) == 2
    assert all(p.returncode is not None and p.stdout.closed for p in procs)


def test_a_bad_system_audio_buffer_is_reported_once():
    errors, got = [], []
    s = SystemSource(on_error=errors.append)
    bad = SimpleNamespace(data=bytes(6), format=fmt(48000, 1, 24, "int"))
    s._on_buffer(bad, got.append)
    s._on_buffer(bad, got.append)
    assert len(errors) == 1 and got == []


def test_system_audio_stop_is_safe_to_call_twice():
    s = SystemSource()

    class Session:
        stops = 0

        def stop(self):
            Session.stops += 1
            if Session.stops > 1:
                raise RuntimeError("Not recording")

    s.session = Session()
    s.stop()
    s.stop()
    assert Session.stops == 1


def lookup_all(name):
    return f"/x/{name}"


def lookup_without_helper(name):
    if name == "callcoach-mic":
        raise FileNotFoundError(name)
    return f"/x/{name}"


def test_the_echo_cancelled_helper_is_preferred(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    kinds = []
    mic = MicSource(on_info=kinds.append)
    assert mic._command() == ["/x/callcoach-mic"] and kinds == ["helper"]


def test_ffmpeg_is_used_without_the_helper(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_without_helper)
    mic = MicSource()
    assert mic._command()[0] == "/x/ffmpeg" and mic.kind == "ffmpeg"


def test_mode_ffmpeg_skips_the_helper(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    assert MicSource(mode="ffmpeg")._command()[0] == "/x/ffmpeg"


def test_mode_helper_needs_the_helper(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_without_helper)
    with pytest.raises(FileNotFoundError):
        MicSource(mode="helper")._command()


class Exited(FakeFfmpeg):
    def __init__(self, data, code):
        super().__init__(data)
        self.returncode = code


def test_a_helper_without_voice_processing_falls_back_to_ffmpeg_at_once(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors, firsts, waits = [], [], []
    mic = MicSource(on_error=errors.append)

    def spawn():
        firsts.append(mic._command()[0])
        if len(firsts) == 1:
            return Exited(b"", 2)
        mic._stop.set()
        return FakeFfmpeg(b"")

    mic._spawn = spawn
    mic._stop.wait = waits.append
    mic._run(lambda chunk: None)
    assert firsts == ["/x/callcoach-mic", "/x/ffmpeg"]
    assert waits == [] and "echo cancellation" in errors[0]


def test_a_device_change_restarts_the_helper_at_once(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors, firsts, waits = [], [], []
    mic = MicSource(on_error=errors.append)

    def spawn():
        firsts.append(mic._command()[0])
        if len(firsts) == 1:
            return Exited(b"", 3)
        mic._stop.set()
        return FakeFfmpeg(b"")

    mic._spawn = spawn
    mic._stop.wait = waits.append
    mic._run(lambda chunk: None)
    assert firsts == ["/x/callcoach-mic", "/x/callcoach-mic"] and waits == []
    assert errors == ["the microphone changed, restarting"]


def test_the_helper_output_becomes_mic_chunks(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    chunks, spawned = [], []
    mic = MicSource(read_ms=20)

    def spawn():
        spawned.append(mic._command()[0])
        if len(spawned) == 1:
            return FakeFfmpeg(b"\x01\x00" * 640)
        mic._stop.set()
        return FakeFfmpeg(b"")

    mic._spawn = spawn
    mic._stop.wait = lambda seconds: None
    mic._run(chunks.append)
    assert [c.channel for c in chunks] == ["me", "me"] and sum(len(c.pcm) for c in chunks) == 1280
    assert spawned[0] == "/x/callcoach-mic" and mic.kind == "helper"


def test_device_changes_in_a_quick_row_back_off(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors, waits, spawned = [], [], []
    mic = MicSource(on_error=errors.append)

    def spawn():
        spawned.append(mic._command()[0])
        if len(spawned) == 5:
            mic._stop.set()
            return FakeFfmpeg(b"")
        return Exited(b"", 3)

    mic._spawn = spawn
    mic._stop.wait = waits.append
    mic._run(lambda chunk: None)
    assert spawned == ["/x/callcoach-mic"] * 5 and waits == [10]
    assert errors == ["the microphone changed, restarting"] * 4


LOUD = np.full(320, 3000, dtype="<i2").tobytes()
ZERO = bytes(640)


def feed(d, pattern, t0=100.0):
    found, t = [], t0
    for chunk in pattern:
        t += 0.02
        got = d.feed(chunk, t)
        if got is not None:
            found.append(round(got, 2))
    return found


def test_a_gap_of_digital_silence_inside_speech_is_reported():
    d = DropoutDetector()
    assert feed(d, [LOUD] * 50 + [ZERO] * 50 + [LOUD] * 10) == [1.0]


def test_silence_between_speech_turns_is_not_a_dropout():
    d = DropoutDetector()
    assert feed(d, [LOUD] * 50 + [ZERO] * 400 + [LOUD] * 10) == []


def test_a_short_zero_run_is_ignored():
    d = DropoutDetector()
    assert feed(d, [LOUD] * 50 + [ZERO] * 5 + [LOUD] * 10) == []


def test_dropouts_are_reported_at_most_once_a_minute():
    d = DropoutDetector()
    pattern = ([LOUD] * 50 + [ZERO] * 50) * 3 + [LOUD] * 10
    assert len(feed(d, pattern)) == 1


class FakeSession:
    def __init__(self):
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def test_a_tap_that_stops_delivering_is_restarted_once_per_10_seconds():
    sessions, errors = [], []

    def open_session(on_buffer):
        sessions.append(FakeSession())
        return sessions[-1]

    now = [100.0]
    s = SystemSource(on_error=errors.append, open_session=open_session, clock=lambda: now[0])
    s.start(lambda chunk: None)
    now[0] = 101.0
    s.check(now[0])
    now[0] = 103.0
    s.check(now[0])
    now[0] = 106.0
    s.check(now[0])
    assert len(sessions) == 2 and sessions[0].stopped
    assert errors == ["system audio stopped delivering, restarting"]


FALLBACK = "the echo-cancelled microphone keeps failing (exit code {}); the microphone uses ffmpeg"


def run_mic(mic, codes, stop_after):
    """Runs the capture loop: each helper start exits with the next code, until `stop_after` starts or the
    first ffmpeg start."""
    firsts, waits = [], []

    def spawn():
        firsts.append(mic._command()[0])
        if len(firsts) >= stop_after or firsts[-1] == "/x/ffmpeg":
            mic._stop.set()
            return FakeFfmpeg(b"")
        return Exited(b"", codes[min(len(firsts), len(codes)) - 1])

    mic._spawn = spawn
    mic._stop.wait = waits.append
    mic._run(lambda chunk: None)
    return firsts, waits


def test_a_helper_that_keeps_changing_falls_back_to_ffmpeg(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors = []
    mic = MicSource(on_error=errors.append)
    firsts, waits = run_mic(mic, [3], stop_after=20)
    assert firsts == ["/x/callcoach-mic"] * 12 + ["/x/ffmpeg"]
    assert waits == [10, 10] and errors.count(FALLBACK.format(3)) == 1 and mic.kind == "ffmpeg"


@pytest.mark.parametrize("code", [4, 5, 6, -11])
def test_a_helper_that_keeps_failing_falls_back_to_ffmpeg(monkeypatch, code):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors = []
    mic = MicSource(on_error=errors.append)
    firsts, waits = run_mic(mic, [code], stop_after=20)
    assert firsts == ["/x/callcoach-mic"] * 3 + ["/x/ffmpeg"]
    assert waits == [10, 10] and errors[-1] == FALLBACK.format(code) and errors.count(FALLBACK.format(code)) == 1


def test_a_helper_that_ran_well_before_failing_is_kept(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    t = [0.0]

    def monotonic():
        t[0] += 60.0
        return t[0]

    mic = MicSource(monotonic=monotonic)
    firsts, waits = run_mic(mic, [4], stop_after=6)
    assert firsts == ["/x/callcoach-mic"] * 6 and waits == []


def test_an_explicit_helper_is_never_replaced(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    mic = MicSource(mode="helper")
    firsts, waits = run_mic(mic, [4], stop_after=5)
    assert firsts == ["/x/callcoach-mic"] * 5 and waits == [10, 10, 10, 10]


def test_an_explicit_helper_without_voice_processing_is_a_fatal_error(monkeypatch):
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors = []
    mic = MicSource(mode="helper", on_error=errors.append)
    firsts, waits = run_mic(mic, [2], stop_after=5)
    assert firsts == ["/x/callcoach-mic"] and waits == []
    assert len(errors) == 1 and errors[0].startswith("microphone failed: ") and "echo cancellation" in errors[0]


class Hung(FakeFfmpeg):
    """A process that keeps its pipe open and writes nothing, until it is signalled."""

    def __init__(self):
        super().__init__(b"")
        self.released = threading.Event()
        self.stdout = SimpleNamespace(read=self._read, close=lambda: None)

    def _read(self, n):
        self.released.wait(5)
        return b""

    def terminate(self):
        self.returncode = -15
        self.released.set()

    def kill(self):
        self.returncode = -9
        self.released.set()


class WroteThenHung(Hung):
    """A process that writes one chunk, then keeps its pipe open and writes nothing."""

    def __init__(self):
        super().__init__()
        self.wrote = False

    def _read(self, n):
        if not self.wrote:
            self.wrote = True
            return b"\x00" * n
        return super()._read(n)


def stall_mic(monkeypatch, first, **kw):
    """A mic whose first process is `first`; the restart stops the source. Returns the mic, procs and errors."""
    monkeypatch.setattr(sources, "tool", lookup_all)
    errors, procs = [], []
    mic = MicSource(on_error=errors.append, clock=lambda: 100.0, **kw)

    def spawn():
        mic._command()
        if procs:
            mic._stop.set()
            procs.append(FakeFfmpeg(b""))
        else:
            procs.append(first)
        return procs[-1]

    mic._spawn = spawn
    mic._stop.wait = lambda seconds: None
    thread = threading.Thread(target=mic._run, args=(lambda chunk: None,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while (mic.proc is None or (isinstance(first, WroteThenHung) and mic._last_data is None)) \
            and time.monotonic() < deadline:
        time.sleep(0.01)
    return mic, procs, errors, thread


def assert_restarted_once(procs, errors, thread):
    thread.join(5)
    assert procs[0].returncode == -15 and len(procs) == 2
    assert errors == ["the microphone stopped delivering audio, restarting"]


def test_ffmpeg_that_stops_writing_is_restarted_after_2_seconds(monkeypatch):
    mic, procs, errors, thread = stall_mic(monkeypatch, WroteThenHung(), mode="ffmpeg")
    mic.check(101.5)
    assert procs[0].returncode is None
    mic.check(102.5)
    assert_restarted_once(procs, errors, thread)


def test_the_helper_gets_4_seconds_to_recover_by_itself(monkeypatch):
    mic, procs, errors, thread = stall_mic(monkeypatch, WroteThenHung())
    mic.check(103.5)
    assert procs[0].returncode is None and mic.kind == "helper"
    mic.check(104.5)
    assert_restarted_once(procs, errors, thread)


def test_a_slow_first_audio_is_not_taken_for_a_stall(monkeypatch):
    mic, procs, errors, thread = stall_mic(monkeypatch, Hung())
    mic.check(109.5)
    assert procs[0].returncode is None and errors == []
    mic.check(110.5)
    assert_restarted_once(procs, errors, thread)


class Stubborn(FakeFfmpeg):
    def terminate(self):
        pass

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("callcoach-mic", timeout)
        return self.returncode


def test_stop_kills_a_mic_process_that_ignores_sigterm():
    mic = MicSource(stop_grace_s=0.01)
    mic.proc = Stubborn(b"")
    mic.stop()
    assert mic.proc.returncode == -9


def test_the_mic_process_writes_its_errors_to_the_call_log(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "tool", lookup_all)
    seen = []

    def popen(argv, **kw):
        seen.append(kw["stderr"].name)
        return FakeFfmpeg(b"")

    monkeypatch.setattr(sources.subprocess, "Popen", popen)
    MicSource(log_dir=tmp_path / "logs")._spawn()
    MicSource(log_dir=tmp_path / "logs", mode="ffmpeg")._spawn()
    assert seen == [str(tmp_path / "logs" / "mic-helper.log"), str(tmp_path / "logs" / "mic-ffmpeg.log")]


def test_a_slow_first_open_is_not_taken_for_a_stall():
    now, sessions = [100.0], []

    def open_session(on_buffer):
        now[0] += 3.0
        sessions.append(FakeSession())
        return sessions[-1]

    s = SystemSource(open_session=open_session, clock=lambda: now[0])
    s.start(lambda chunk: None)
    s.check(now[0] + 0.5)
    assert len(sessions) == 1


def test_a_tap_that_could_not_reopen_is_tried_again_later():
    now, sessions, errors, failing = [100.0], [], [], [False]

    def open_session(on_buffer):
        if failing[0]:
            raise RuntimeError("no tap")
        sessions.append(FakeSession())
        return sessions[-1]

    s = SystemSource(on_error=errors.append, open_session=open_session, clock=lambda: now[0])
    s.start(lambda chunk: None)
    failing[0] = True
    now[0] = 103.0
    s.check(now[0])
    failing[0] = False
    now[0] = 114.0
    s.check(now[0])
    assert len(sessions) == 2 and s.session is sessions[1]
    assert errors == ["system audio stopped delivering, restarting", "system audio could not restart: no tap"]
    s.stop()
    now[0] = 200.0
    s.check(now[0])
    assert len(sessions) == 2
