"""Where audio comes from: a recorded file, the microphone, or what the Mac plays."""
from __future__ import annotations

import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from callcoach.config import RATE, SAMPLE_BYTES, tool
from callcoach.data import ME, THEM, Chunk

Sink = Callable[[Chunk], None]

HELPER = "callcoach-mic"
HELPER_NO_VOICE_PROCESSING = 2
HELPER_DEVICE_CHANGED = 3
QUICK_CHANGES = 3
QUICK_S = 5.0
BACKOFF_S = 10
MAX_BACKOFFS = 2
FIRST_DATA_S = 10.0
HELPER_STALL_S = 4.0


def _end(proc) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    proc.stdout.close()


class FileSource:
    """Plays a 16 kHz WAV (stereo: left = me, right = them; mono: them) as if it were live."""

    def __init__(self, path: Path, *, realtime: bool = True, chunk_ms: int = 20,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.path = path
        self.realtime = realtime
        self.chunk_ms = chunk_ms
        self.clock = clock
        self.sleep = sleep
        self.t0: float | None = None
        self.error: Exception | None = None
        self.done = threading.Event()
        self._stop = threading.Event()

    def start(self, sink: Sink) -> None:
        self.t0 = self.clock()
        threading.Thread(target=self._run, args=(sink,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, sink: Sink) -> None:
        try:
            self._play(sink)
        except Exception as e:
            self.error = e
        finally:
            self.done.set()

    def _play(self, sink: Sink) -> None:
        with wave.open(str(self.path)) as w:
            channels = w.getnchannels()
            if w.getframerate() != RATE or w.getsampwidth() != SAMPLE_BYTES or channels not in (1, 2):
                raise ValueError(f"{self.path} must be 16 kHz 16 bit mono or stereo")
            per_chunk = RATE * self.chunk_ms // 1000
            pos = 0
            while not self._stop.is_set():
                data = w.readframes(per_chunk)
                if not data:
                    return
                samples = np.frombuffer(data, dtype="<i2")
                pos += len(samples) // channels
                t_end = self.t0 + pos / RATE
                if self.realtime:
                    delay = t_end - self.clock()
                    if delay > 0:
                        self.sleep(delay)
                if channels == 1:
                    sink(Chunk(THEM, samples.tobytes(), t_end))
                    continue
                sink(Chunk(ME, samples[0::2].tobytes(), t_end))
                sink(Chunk(THEM, samples[1::2].tobytes(), t_end))


class MicSource:
    """The default microphone. Prefers callcoach-mic (Apple voice processing, echo cancelled, off when the sound goes
    to headphones, which have no echo and then keep the other apps' volume); falls back
    to ffmpeg when the helper is missing, voice processing is not available, or (mode auto) the helper keeps
    failing. Restarts when the device changes, the capture stops, or the process stops writing. The first
    audio may take a few seconds (voice processing starts slowly); the helper gets longer to recover from a
    device change by itself before it counts as stalled."""

    def __init__(self, *, device: str = "default", on_error: Callable[[str], None] = lambda m: None,
                 on_info: Callable[[str], None] = lambda m: None, read_ms: int = 20, mode: str = "auto",
                 log_dir: Path | None = None, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, stall_s: float = 2.0,
                 helper_stall_s: float = HELPER_STALL_S, first_data_s: float = FIRST_DATA_S,
                 stop_grace_s: float = 2.0):
        self.device = device
        self.on_error = on_error
        self.on_info = on_info
        self.mode = mode
        self.log_dir = log_dir
        self.clock = clock
        self.monotonic = monotonic
        self.stall_s = stall_s
        self.helper_stall_s = helper_stall_s
        self.first_data_s = first_data_s
        self.stop_grace_s = stop_grace_s
        self.kind: str | None = None
        self.read_bytes = RATE * SAMPLE_BYTES * read_ms // 1000
        self.proc: subprocess.Popen | None = None
        self._helper_failed = False
        self._quick_changes = 0
        self._backoffs = 0
        self._spawned: float | None = None
        self._last_data: float | None = None
        self._stalled = False
        self._stop = threading.Event()

    def start(self, sink: Sink) -> None:
        threading.Thread(target=self._run, args=(sink,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(self.stop_grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()

    def check(self, now: float) -> None:
        """The helper and ffmpeg write all the time, silence included: no bytes for a while is a stall."""
        proc, spawned, last = self.proc, self._spawned, self._last_data
        if proc is None or spawned is None or self._stalled or self._stop.is_set() or proc.poll() is not None:
            return
        if last is None:
            waited, limit = now - spawned, self.first_data_s
        else:
            waited, limit = now - last, self.helper_stall_s if self.kind == "helper" else self.stall_s
        if waited > limit:
            self._stalled = True
            proc.terminate()
            timer = threading.Timer(self.stop_grace_s, lambda: proc.poll() is None and proc.kill())
            timer.daemon = True
            timer.start()

    def _command(self) -> list[str]:
        if self.mode != "ffmpeg" and not self._helper_failed:
            try:
                path = tool(HELPER)
            except FileNotFoundError:
                if self.mode == "helper":
                    raise
            else:
                self._chose("helper")
                return [path]
        self._chose("ffmpeg")
        return [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-fflags", "nobuffer",
                "-f", "avfoundation", "-i", f":{self.device}", "-ac", "1", "-ar", str(RATE),
                "-f", "s16le", "-flush_packets", "1", "pipe:1"]

    def _chose(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.on_info(kind)

    def _log(self):
        if self.log_dir is None:
            return subprocess.DEVNULL
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            return open(self.log_dir / f"mic-{self.kind}.log", "ab")
        except OSError:
            return subprocess.DEVNULL

    def _spawn(self) -> subprocess.Popen:
        command = self._command()
        log = self._log()
        try:
            return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                    start_new_session=True)
        finally:
            if log is not subprocess.DEVNULL:
                log.close()

    def _run(self, sink: Sink) -> None:
        try:
            self._capture(sink)
        except Exception as e:
            self.on_error(f"microphone failed: {e}")

    def _capture(self, sink: Sink) -> None:
        while not self._stop.is_set():
            started = self.monotonic()
            self._stalled = False
            self._spawned, self._last_data = self.clock(), None
            proc = self.proc = self._spawn()
            try:
                while not self._stop.is_set():
                    data = proc.stdout.read(self.read_bytes)
                    if not data:
                        break
                    self._last_data = self.clock()
                    sink(Chunk(ME, data, time.time()))
            finally:
                _end(proc)
            if self._stop.is_set():
                return
            self._after_exit(proc.returncode, self.monotonic() - started < QUICK_S)

    def _after_exit(self, code: int, quick: bool) -> None:
        """Restarts at once, backs off, or leaves a failing helper for ffmpeg."""
        if not quick:
            self._backoffs = 0
        helper = self.kind == "helper"
        if helper and code == HELPER_NO_VOICE_PROCESSING:
            if self.mode == "helper":
                raise RuntimeError(f"echo cancellation is not available on this Mac (helper exit code {code})")
            self._helper_failed = True
            self.on_error("echo cancellation is not available on this Mac; the microphone uses ffmpeg")
            return
        if helper and code == HELPER_DEVICE_CHANGED and not self._stalled:
            self.on_error("the microphone changed, restarting")
            self._quick_changes = self._quick_changes + 1 if quick else 0
            if self._quick_changes > QUICK_CHANGES:
                self._quick_changes = 0
                self._back_off(code)
            return
        if self._stalled:
            self.on_error("the microphone stopped delivering audio, restarting")
        else:
            self.on_error(f"microphone stopped ({self.kind or 'ffmpeg'} exit code {code}), restarting")
        if quick:
            self._back_off(code)

    def _back_off(self, code: int) -> None:
        if self.kind == "helper" and self.mode == "auto" and self._backoffs >= MAX_BACKOFFS:
            self._helper_failed = True
            self._backoffs = 0
            self.on_error(f"the echo-cancelled microphone keeps failing (exit code {code}); "
                          "the microphone uses ffmpeg")
            return
        self._backoffs += 1
        self._stop.wait(BACKOFF_S)


class DropoutDetector:
    """Finds digital silence inside speech: the tap dropping audio during an output device change."""

    def __init__(self, min_s: float = 0.3, max_s: float = 5.0, near_s: float = 0.5, every_s: float = 60.0,
                 audible: int = 100):
        self.min_s, self.max_s, self.near_s, self.every_s = min_s, max_s, near_s, every_s
        self.audible = audible
        self.last_audible: float | None = None
        self.zero_since: float | None = None
        self.reported: float | None = None

    def feed(self, pcm: bytes, t_end: float) -> float | None:
        x = np.frombuffer(pcm, dtype="<i2")
        if not len(x):
            return None
        t_start = t_end - len(x) / RATE
        if not x.any():
            if self.zero_since is None:
                self.zero_since = t_start
            return None
        found = None
        if self.zero_since is not None:
            length = t_start - self.zero_since
            near = self.last_audible is not None and self.zero_since - self.last_audible <= self.near_s
            loud = int(np.abs(x).max()) > self.audible
            fresh = self.reported is None or t_end - self.reported >= self.every_s
            if near and loud and fresh and self.min_s <= length <= self.max_s:
                found, self.reported = length, t_end
            self.zero_since = None
        if int(np.abs(x).max()) > self.audible:
            self.last_audible = t_end
        return found


def _catap_session(on_buffer):
    from catap import record_system_audio

    return record_system_audio(None, mono=True, on_buffer=on_buffer)


class SystemSource:
    """Everything the Mac plays, through a private Core Audio tap (catap), as 16 kHz mono. Restarts a tap
    that stops delivering and reports dropouts of digital silence inside speech."""

    def __init__(self, *, on_error: Callable[[str], None] = lambda m: None,
                 on_dropout: Callable[[float], None] = lambda s: None, open_session=_catap_session,
                 clock: Callable[[], float] = time.time, stall_s: float = 2.0, retry_s: float = 10.0):
        self.on_error = on_error
        self.on_dropout = on_dropout
        self.open_session = open_session
        self.clock = clock
        self.stall_s = stall_s
        self.retry_s = retry_s
        self.session = None
        self.dropouts = DropoutDetector()
        self._sink: Sink | None = None
        self._last: float | None = None
        self._restarted: float | None = None
        self._rate: float | None = None
        self._resampler = None
        self._failed = False
        self._broken = False
        self._stopped = False

    def start(self, sink: Sink) -> None:
        self._sink = sink
        self._open()
        self._last = self.clock()

    def _open(self) -> None:
        self.session = self.open_session(lambda buf: self._on_buffer(buf, self._sink))
        self.session.start()

    def stop(self) -> None:
        self._stopped = True
        self._close()

    def _close(self) -> None:
        session, self.session = self.session, None
        if session:
            try:
                session.stop()
            except Exception as e:
                self.on_error(f"system audio did not stop cleanly: {e}")

    def check(self, now: float) -> None:
        """Restarts a tap that stopped delivering, and tries again later when it could not be reopened."""
        if self._stopped or self._last is None or now - self._last <= self.stall_s:
            return
        if self.session is None and not self._broken:
            return
        if self._restarted is not None and now - self._restarted < self.retry_s:
            return
        self._restarted = now
        if self.session is not None:
            self.on_error("system audio stopped delivering, restarting")
            self._close()
        try:
            self._open()
            self._last = self.clock()
            self._broken = False
        except Exception as e:
            self._broken = True
            self.on_error(f"system audio could not restart: {e}")

    def _on_buffer(self, buf, sink: Sink) -> None:
        try:
            pcm = self._convert(buf)
            if pcm:
                t = self.clock()
                self._last = t
                gap = self.dropouts.feed(pcm, t)
                if gap is not None:
                    self.on_dropout(gap)
                sink(Chunk(THEM, pcm, t))
        except Exception as e:
            if not self._failed:
                self._failed = True
                self.on_error(f"system audio buffer failed: {e}")

    def _convert(self, buf) -> bytes:
        import soxr

        f = buf.format
        if f.is_float and f.bits_per_sample == 32:
            x = np.frombuffer(buf.data, dtype="<f4")
        elif f.is_signed_integer and f.bits_per_sample == 16:
            x = np.frombuffer(buf.data, dtype="<i2").astype(np.float32) / 32768.0
        elif f.is_signed_integer and f.bits_per_sample == 32:
            x = np.frombuffer(buf.data, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"unsupported system audio format: {f}")
        if f.num_channels > 1:
            x = x.reshape(-1, f.num_channels).mean(axis=1)
        if self._rate != f.sample_rate:
            self._rate = f.sample_rate
            self._resampler = soxr.ResampleStream(f.sample_rate, RATE, 1, dtype="float32", quality="HQ")
        y = self._resampler.resample_chunk(np.ascontiguousarray(x, dtype=np.float32))
        return (np.clip(y, -1.0, 1.0) * 32767).astype("<i2").tobytes()
