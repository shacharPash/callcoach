"""me.wav and them.wav during the call; one verified call.m4a after it."""
from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from callcoach import wav
from callcoach.config import RATE, SAMPLE_BYTES, tool
from callcoach.data import CHANNELS, Chunk

GAP_TOLERANCE_SECONDS = 0.25
DURATION_TOLERANCE_SECONDS = 1.0
FFPROBE_TIMEOUT_SECONDS = 30.0
LOCK_NAME = ".recording"


@dataclass(frozen=True)
class FinalizeResult:
    ok: bool
    seconds: float = 0.0
    reason: str = ""


def ps_env() -> dict:
    """The environment for ps: its dates are the same whatever the caller's locale and time zone."""
    return {"PATH": os.environ.get("PATH") or "/bin:/usr/bin", "LC_ALL": "C", "TZ": "UTC"}


def process_start(pid: int) -> str | None:
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=5, env=ps_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return " ".join(out.stdout.split()) or None


class Recorder:
    def __init__(self, folder: Path, started: float):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.started = started
        pid = os.getpid()
        (folder / LOCK_NAME).write_text(f"{pid} {process_start(pid) or ''}".strip())
        self.writers = {ch: wav.WavWriter(folder / f"{ch}.wav") for ch in CHANNELS}

    def write(self, chunk: Chunk) -> None:
        writer = self.writers[chunk.channel]
        samples = len(chunk.pcm) // SAMPLE_BYTES
        start = round((chunk.t_end - self.started) * RATE) - samples
        gap = start - writer.data_bytes // SAMPLE_BYTES
        if gap > GAP_TOLERANCE_SECONDS * RATE:
            writer.write(bytes(gap * SAMPLE_BYTES))
        writer.write(chunk.pcm)

    def close(self) -> None:
        first: OSError | None = None
        try:
            longest = max(w.data_bytes for w in self.writers.values())
            for w in self.writers.values():
                if w.data_bytes < longest:
                    w.write(bytes(longest - w.data_bytes))
        finally:
            for w in self.writers.values():
                try:
                    w.close()
                except OSError as e:
                    first = first or e
            if first is not None:
                raise first

    def finalize(self) -> FinalizeResult:
        self.close()
        return finalize_folder(self.folder)

    def release(self) -> None:
        (self.folder / LOCK_NAME).unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recording_in_progress(folder: Path) -> bool:
    try:
        pid_text, _, start = (folder / LOCK_NAME).read_text().partition(" ")
        pid = int(pid_text)
    except (FileNotFoundError, ValueError):
        return False
    if not _alive(pid):
        return False
    if not start:
        return True
    now = process_start(pid)
    return now is None or now == start


def probe_seconds(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=FFPROBE_TIMEOUT_SECONDS, start_new_session=True,
        )
        value = float(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def finalize_folder(folder: Path) -> FinalizeResult:
    me, them, out = folder / "me.wav", folder / "them.wav", folder / "call.m4a"
    if out.exists():
        me.unlink(missing_ok=True)
        them.unlink(missing_ok=True)
        return FinalizeResult(True, seconds=probe_seconds(out) or 0.0)
    present = [p for p in (me, them) if p.exists()]
    if not present:
        return FinalizeResult(False, reason="no recording found")
    expected = max(wav.seconds(p) for p in present)
    if expected == 0:
        me.unlink(missing_ok=True)
        them.unlink(missing_ok=True)
        return FinalizeResult(False, reason="no audio was recorded")
    tmp = folder / ".call.tmp.m4a"
    try:
        proc = subprocess.run(
            [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-i", str(me), "-i", str(them),
             "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]", "-map", "[a]",
             "-c:a", "aac", "-b:a", "64k", str(tmp)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=max(120.0, expected), start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        tmp.unlink(missing_ok=True)
        return FinalizeResult(False, reason=f"ffmpeg did not finish: {e}")
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        return FinalizeResult(False, reason=f"ffmpeg failed: {proc.stderr.strip()[-300:]}")
    got = probe_seconds(tmp)
    if got is None or abs(got - expected) > DURATION_TOLERANCE_SECONDS:
        tmp.unlink(missing_ok=True)
        return FinalizeResult(False, reason=f"duration mismatch: wav {expected:.1f}s, m4a {got}")
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    tmp.replace(out)
    me.unlink(missing_ok=True)
    them.unlink(missing_ok=True)
    return FinalizeResult(True, seconds=got)


def needs_recovery(folder: Path) -> bool:
    leftovers = [folder / LOCK_NAME] + [folder / f"{ch}.wav" for ch in CHANNELS]
    return not recording_in_progress(folder) and any(p.exists() for p in leftovers)


def recover_folder(folder: Path) -> FinalizeResult:
    if recording_in_progress(folder):
        return FinalizeResult(False, reason="recording in progress")
    try:
        if not (folder / "call.m4a").exists():
            _repair_wavs(folder)
        return finalize_folder(folder)
    finally:
        (folder / LOCK_NAME).unlink(missing_ok=True)


def _repair_wavs(folder: Path) -> None:
    paths = [folder / f"{ch}.wav" for ch in CHANNELS]
    for p in paths:
        if not p.exists():
            p.write_bytes(wav.header(0))
        wav.repair(p)
    longest = max(p.stat().st_size for p in paths)
    for p in paths:
        missing = longest - p.stat().st_size
        if missing:
            with open(p, "ab") as f:
                f.write(bytes(missing))
            wav.repair(p)
