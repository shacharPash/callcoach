import os
import subprocess
import sys
import wave

import pytest
from audio_helpers import has_ffmpeg, tone

from callcoach import recorder as rec_mod
from callcoach import wav
from callcoach.data import ME, THEM, Chunk
from callcoach.recorder import Recorder, finalize_folder, needs_recovery, recover_folder

needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", ""])
    p.wait()
    return p.pid


def frames(path):
    with wave.open(str(path)) as r:
        return r.getnframes()


def test_channels_are_aligned_to_the_clock(tmp_path):
    r = Recorder(tmp_path, started=100.0)
    r.write(Chunk(ME, tone(1), t_end=101.0))
    r.write(Chunk(THEM, tone(1), t_end=103.0))
    r.close()
    assert frames(tmp_path / "me.wav") == frames(tmp_path / "them.wav") == 48000


def test_small_jitter_is_not_padded(tmp_path):
    r = Recorder(tmp_path, started=100.0)
    r.write(Chunk(ME, tone(1), t_end=101.1))
    r.write(Chunk(ME, tone(1), t_end=102.0))
    r.close()
    assert frames(tmp_path / "me.wav") == 32000


@needs_ffmpeg
def test_finalize_writes_m4a_and_deletes_the_wavs(tmp_path):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(3), t_end=3.0))
    r.write(Chunk(THEM, tone(2), t_end=2.0))
    res = r.finalize()
    assert res.ok and abs(res.seconds - 3.0) < 0.2
    assert (tmp_path / "call.m4a").exists()
    assert not (tmp_path / "me.wav").exists() and not (tmp_path / "them.wav").exists()


@needs_ffmpeg
def test_wavs_are_kept_when_the_duration_does_not_match(tmp_path, monkeypatch):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(2), t_end=2.0))
    monkeypatch.setattr(rec_mod, "probe_seconds", lambda p: 0.5)
    res = r.finalize()
    assert not res.ok and "duration mismatch" in res.reason
    assert (tmp_path / "me.wav").exists() and not (tmp_path / "call.m4a").exists()
    assert not (tmp_path / ".call.tmp.m4a").exists()


@needs_ffmpeg
def test_recover_after_a_crash(tmp_path):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(2), t_end=2.0))
    r.write(Chunk(THEM, tone(1), t_end=1.0))
    for w in r.writers.values():
        w._f.flush()
    (tmp_path / ".recording").write_text(str(dead_pid()))
    assert needs_recovery(tmp_path)
    res = recover_folder(tmp_path)
    assert res.ok and abs(res.seconds - 2.0) < 0.2
    assert not needs_recovery(tmp_path)


def finished_call(tmp_path):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(2), t_end=2.0))
    r.write(Chunk(THEM, tone(2), t_end=2.0))
    assert r.finalize().ok
    return r


@needs_ffmpeg
def test_a_second_finalize_is_ok(tmp_path):
    finished_call(tmp_path)
    res = finalize_folder(tmp_path)
    assert res.ok and (tmp_path / "call.m4a").exists()


@needs_ffmpeg
def test_recovery_never_replaces_a_finished_m4a(tmp_path):
    finished_call(tmp_path)
    before = (tmp_path / "call.m4a").read_bytes()
    (tmp_path / "them.wav").write_bytes(wav.pcm_to_wav(tone(1)))
    (tmp_path / ".recording").write_text(str(dead_pid()))
    res = recover_folder(tmp_path)
    assert res.ok
    assert (tmp_path / "call.m4a").read_bytes() == before
    assert not (tmp_path / "them.wav").exists() and not (tmp_path / "me.wav").exists()


def broken_finalize_keeps_wavs(tmp_path):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(1), t_end=1.0))
    r.write(Chunk(THEM, tone(1), t_end=1.0))
    res = r.finalize()
    assert not res.ok and "ffmpeg did not finish" in res.reason
    assert (tmp_path / "me.wav").exists() and (tmp_path / "them.wav").exists()
    assert not (tmp_path / ".call.tmp.m4a").exists() and not (tmp_path / "call.m4a").exists()


def test_a_missing_ffmpeg_keeps_the_wavs(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "tool", lambda name: f"/nonexistent/{name}")
    broken_finalize_keeps_wavs(tmp_path)


def test_a_hung_ffmpeg_keeps_the_wavs(tmp_path, monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(rec_mod.subprocess, "run", hang)
    broken_finalize_keeps_wavs(tmp_path)


def test_a_live_recording_is_not_recovered(tmp_path):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(1), t_end=1.0))
    r.write(Chunk(THEM, tone(1), t_end=1.0))
    sizes = {p.name: p.stat().st_size for p in tmp_path.glob("*.wav")}
    assert not needs_recovery(tmp_path)
    res = recover_folder(tmp_path)
    assert not res.ok and "in progress" in res.reason
    assert {p.name: p.stat().st_size for p in tmp_path.glob("*.wav")} == sizes
    r.close()


def test_finalize_without_any_wav(tmp_path):
    res = finalize_folder(tmp_path)
    assert not res.ok and "no recording found" in res.reason


def test_the_lock_is_written_before_the_wavs(tmp_path, monkeypatch):
    real = wav.WavWriter
    seen = []

    def writer(path):
        seen.append((tmp_path / ".recording").exists())
        return real(path)

    monkeypatch.setattr(wav, "WavWriter", writer)
    Recorder(tmp_path, started=0.0).close()
    assert seen == [True, True]


@needs_ffmpeg
def test_finalize_keeps_the_lock_until_the_call_is_released(tmp_path):
    r = finished_call(tmp_path)
    assert (tmp_path / ".recording").exists() and not needs_recovery(tmp_path)
    r.release()
    assert not (tmp_path / ".recording").exists() and not needs_recovery(tmp_path)


@needs_ffmpeg
def test_a_crash_after_finalize_is_recovered(tmp_path):
    finished_call(tmp_path)
    (tmp_path / ".recording").write_text(str(dead_pid()))
    assert needs_recovery(tmp_path)
    assert recover_folder(tmp_path).ok
    assert not (tmp_path / ".recording").exists() and not needs_recovery(tmp_path)


def test_a_reused_pid_does_not_block_recovery(tmp_path, monkeypatch):
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(1), t_end=1.0))
    r.close()
    monkeypatch.setattr(rec_mod, "process_start", lambda pid: "Sat Nov 14 09:00:00 2026")
    assert needs_recovery(tmp_path)


def test_the_lock_names_the_process_and_its_start(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "process_start", lambda pid: "Sun Nov 15 10:00:00 2026")
    Recorder(tmp_path, started=0.0)
    pid, start = (tmp_path / ".recording").read_text().split(" ", 1)
    assert int(pid) > 0 and start == "Sun Nov 15 10:00:00 2026"


def test_an_old_lock_with_only_a_pid_still_works(tmp_path):
    (tmp_path / ".recording").write_text(str(os.getpid()))
    assert rec_mod.recording_in_progress(tmp_path)


def test_the_start_time_is_read_in_a_fixed_locale_and_time_zone(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw["env"]))
        return subprocess.CompletedProcess(argv, 0, stdout="Sun Nov 15 10:00:00 2026\n", stderr="")

    monkeypatch.setenv("LANG", "he_IL.UTF-8")
    monkeypatch.setenv("LC_ALL", "he_IL.UTF-8")
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    monkeypatch.setattr(rec_mod.subprocess, "run", run)
    assert rec_mod.process_start(123) == "Sun Nov 15 10:00:00 2026"
    [(argv, env)] = seen
    assert argv == ["ps", "-p", "123", "-o", "lstart="]
    assert env["LC_ALL"] == "C" and env["TZ"] == "UTC" and env["PATH"] and "LANG" not in env


def test_the_start_time_does_not_depend_on_the_callers_locale(monkeypatch):
    monkeypatch.setenv("LC_ALL", "he_IL.UTF-8")
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    local = rec_mod.process_start(os.getpid())
    if local is None:
        pytest.skip("ps cannot read processes here")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("TZ", "UTC")
    assert rec_mod.process_start(os.getpid()) == local and local.isascii()


def test_a_live_pid_whose_start_cannot_be_read_is_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "process_start", lambda pid: "Sun Nov 15 10:00:00 2026")
    r = Recorder(tmp_path, started=0.0)
    r.write(Chunk(ME, tone(1), t_end=1.0))
    sizes = {p.name: p.stat().st_size for p in tmp_path.glob("*.wav")}
    monkeypatch.setattr(rec_mod, "process_start", lambda pid: None)
    assert rec_mod.recording_in_progress(tmp_path) and not needs_recovery(tmp_path)
    assert recover_folder(tmp_path).reason == "recording in progress"
    assert {p.name: p.stat().st_size for p in tmp_path.glob("*.wav")} == sizes
    r.close()
