import os
import signal
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import pytest

from callcoach.wav import WavWriter, pcm_to_wav, repair, seconds

ENGINE = Path(__file__).resolve().parents[1] / "engine"


def nframes(path):
    with wave.open(str(path)) as r:
        return r.getnframes()


def test_writer_produces_a_valid_mono_16k_wav(tmp_path):
    w = WavWriter(tmp_path / "a.wav")
    w.write(b"\x01\x00" * 16000)
    w.close()
    with wave.open(str(tmp_path / "a.wav")) as r:
        assert (r.getnchannels(), r.getframerate(), r.getsampwidth(), r.getnframes()) == (1, 16000, 2, 16000)


def test_header_is_refreshed_while_writing(tmp_path):
    w = WavWriter(tmp_path / "a.wav", refresh_seconds=1.0)
    w.write(bytes(32000 * 2))
    assert nframes(tmp_path / "a.wav") == 32000
    w.close()


def test_repair_fixes_a_file_left_by_a_crash(tmp_path):
    p = tmp_path / "a.wav"
    w = WavWriter(p, refresh_seconds=100)
    w.write(bytes(16000 * 2 * 3))
    with open(p, "ab") as f:
        f.write(b"\x00")
    repair(p)
    assert nframes(p) == 48000
    assert abs(seconds(p) - 3.0) < 0.001


def test_pcm_to_wav_roundtrip(tmp_path):
    p = tmp_path / "b.wav"
    p.write_bytes(pcm_to_wav(b"\x00\x00" * 800))
    assert nframes(p) == 800


def test_audio_survives_a_hard_kill(tmp_path):
    p = tmp_path / "a.wav"
    script = textwrap.dedent(f"""
        import os, signal
        from pathlib import Path
        from callcoach.wav import WavWriter
        w = WavWriter(Path({str(p)!r}))
        for _ in range(100):
            w.write(bytes(640))
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    proc = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH": str(ENGINE)})
    assert proc.returncode == -signal.SIGKILL
    repair(p)
    assert abs(seconds(p) - 2.0) < 0.001


def test_writes_after_a_header_refresh_are_appended(tmp_path):
    w = WavWriter(tmp_path / "a.wav", refresh_seconds=1.0)
    w.write(bytes(48000))
    w.write(bytes(48000))
    w.close()
    assert (tmp_path / "a.wav").stat().st_size == 44 + 96000
    assert nframes(tmp_path / "a.wav") == 48000


def test_repair_turns_a_stub_into_an_empty_wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RI")
    repair(p)
    repair(p)
    assert nframes(p) == 0 and p.stat().st_size == 44


def test_close_is_idempotent_and_writing_after_close_fails(tmp_path):
    w = WavWriter(tmp_path / "a.wav")
    w.close()
    w.close()
    with pytest.raises(ValueError):
        w.write(b"\x00\x00")


def test_odd_length_pcm_is_rejected(tmp_path):
    w = WavWriter(tmp_path / "a.wav")
    with pytest.raises(ValueError):
        w.write(b"\x00")
    w.close()
