"""whisper.cpp whisper-server: an HTTP client and the local server process."""
from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from callcoach.recorder import process_start, ps_env
from callcoach.wav import pcm_to_wav

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def multipart(fields: dict[str, str], wav_bytes: bytes) -> tuple[bytes, str]:
    boundary = "callcoach" + uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="segment.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode() + wav_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def write_pid_file(run_dir: Path, *, pid: int, owner: int, port: int | None = None,
                   start: str | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"whisper-{pid}.json"
    path.write_text(json.dumps({"pid": pid, "owner": owner, "port": port, "start": start}))
    return path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ps(pid: int, field: str) -> str | None:
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", f"{field}="], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=5, env=ps_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _command_of(pid: int) -> str | None:
    return _ps(pid, "command")


def _binary_of(pid: int) -> str | None:
    return _ps(pid, "comm")


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def reap_orphans(run_dir: Path, *, alive=_alive, command_of=_command_of, binary_of=_binary_of,
                 start_of=process_start, kill=os.kill) -> list[int]:
    """Stops whisper servers whose engine is gone (a hard kill leaves them running).

    A pid is signalled only while it still runs whisper-server on the recorded port and started at the
    recorded time, so a pid the system gave to another process is left alone.
    """
    reaped = []
    for path in sorted(run_dir.glob("whisper-*.json")) if run_dir.exists() else []:
        try:
            data = json.loads(path.read_text())
            pid, owner = int(data["pid"]), int(data["owner"])
            port, start = int(data.get("port") or 0), data.get("start")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            _remove(path)
            continue
        if alive(owner):
            continue
        ours = (port and start and Path(binary_of(pid) or "").name == "whisper-server"
                and f" --port {port} " in f"{command_of(pid) or ''} " and start_of(pid) == start)
        if ours:
            try:
                kill(pid, signal.SIGTERM)
                reaped.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        _remove(path)
    return reaped


class WhisperClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def transcribe(self, pcm: bytes, lang: str, prompt: str = "") -> str:
        fields = {"language": lang, "response_format": "json", "temperature": "0.0", "no_timestamps": "true"}
        if prompt:
            fields["prompt"] = prompt
        return str(self._post(fields, pcm).get("text", "")).strip()

    def segments(self, pcm: bytes, lang: str, prompt: str = "") -> list[tuple[float, float, str]]:
        """(start, end, text) of each sentence Whisper found in the audio, in seconds from its start."""
        fields = {"language": lang, "response_format": "verbose_json", "temperature": "0.0", "no_timestamps": "false"}
        if prompt:
            fields["prompt"] = prompt
        return [(float(s["start"]), float(s["end"]), str(s.get("text", "")).strip())
                for s in self._post(fields, pcm).get("segments", []) if str(s.get("text", "")).strip()]

    def detect(self, pcm: bytes) -> dict[str, float]:
        resp = self._post({"response_format": "verbose_json", "detect_language": "true"}, pcm)
        return {k: float(v) for k, v in resp.get("language_probabilities", {}).items()}

    def _post(self, fields: dict[str, str], pcm: bytes) -> dict:
        body, content_type = multipart(fields, pcm_to_wav(pcm))
        req = urllib.request.Request(self.base_url + "/inference", data=body, method="POST",
                                     headers={"Content-Type": content_type})
        with _OPENER.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class WhisperServer:
    def __init__(self, name: str, model: Path, *, binary: str, lang: str | None = None,
                 vad_model: Path | None = None, threads: int = 4, log_dir: Path | None = None,
                 run_dir: Path | None = None, on_note: Callable[..., None] = lambda kind, **fields: None):
        self.name = name
        self.model = model
        self.binary = binary
        self.lang = lang
        self.vad_model = vad_model
        self.threads = threads
        self.log_dir = log_dir
        self.run_dir = run_dir
        self.on_note = on_note
        self.pid_file: Path | None = None
        self.port = 0
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log_path(self) -> Path | None:
        return self.log_dir / f"whisper-{self.name}.log" if self.log_dir else None

    def start(self) -> None:
        self.port = free_port()
        cmd = [self.binary, "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
               "-t", str(self.threads), "-nt"]
        if self.lang:
            cmd += ["-l", self.lang]
        if self.vad_model:
            cmd += ["--vad", "-vm", str(self.vad_model)]
        log = subprocess.DEVNULL
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log = open(self.log_path, "ab")
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         start_new_session=True)
        finally:
            if log is not subprocess.DEVNULL:
                log.close()
        if self.run_dir:
            self._write_pid_file()

    def _write_pid_file(self) -> None:
        """Lets a later engine stop this server after a hard kill; transcription goes on without it."""
        old, self.pid_file = self.pid_file, None
        try:
            if old:
                old.unlink(missing_ok=True)
            self.pid_file = write_pid_file(self.run_dir, pid=self.proc.pid, owner=os.getpid(), port=self.port,
                                           start=process_start(self.proc.pid))
        except OSError as e:
            self.on_note("pid_file_failed", server=self.name, error=type(e).__name__, message=str(e)[:200])

    def wait_ready(self, timeout: float = 120.0, attempts: int = 3) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is None or self.proc.poll() is not None:
                attempts -= 1
                if attempts <= 0:
                    code = self.proc.returncode if self.proc else None
                    raise RuntimeError(f"whisper-server {self.name} exited before it was ready "
                                       f"(code {code}, log {self.log_path})")
                self.start()
                continue
            try:
                with _OPENER.open(self.url + "/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException):
                pass
            time.sleep(0.2)
        self.stop()
        raise TimeoutError(f"whisper-server {self.name} not ready after {timeout:.0f} s (log {self.log_path})")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.pid_file:
            self.pid_file.unlink(missing_ok=True)
            self.pid_file = None

    def restart(self) -> None:
        self.stop()
        self.start()
        self.wait_ready()
