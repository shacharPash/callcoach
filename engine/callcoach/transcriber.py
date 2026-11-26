"""Whisper servers for Hebrew, English and language id; a crashed server is restarted once."""
from __future__ import annotations

import http.client
import threading
import urllib.error
from pathlib import Path
from typing import Callable, Protocol

from callcoach.config import RUN_DIR, model_path, tool
from callcoach.whisper import WhisperClient, WhisperServer

_ERRORS = (urllib.error.URLError, ConnectionError, http.client.HTTPException)


def glossary_prompt(terms) -> str:
    """Names and terms of the call, sent to Whisper as its prompt to bias the spelling."""
    words = [t.strip() for t in terms if t and t.strip()]
    return ", ".join(words)[:224].rstrip(", ")


class Transcriber(Protocol):
    def detect(self, pcm: bytes) -> dict[str, float] | None: ...

    def transcribe(self, pcm: bytes, lang: str) -> str: ...


class TranscriptionDown(RuntimeError):
    pass


class DownTranscriber:
    """Stands in when the whisper servers did not start: the call records without lines."""

    down = True

    def __init__(self, reason: str):
        self.reason = reason

    def detect(self, pcm: bytes) -> dict[str, float] | None:
        return None

    def transcribe(self, pcm: bytes, lang: str) -> str:
        raise TranscriptionDown(self.reason)

    def set_glossary(self, terms) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class WarmingTranscriber:
    """Stands in from the start of the engine while the whisper servers warm up, so a call records from the click:
    a request waits until the servers are ready (or turned out down) and the requests go on in order. It is set
    exactly once, when the warm-up ends either way."""

    def __init__(self):
        self._ready = threading.Event()
        self._inner = None
        self._glossary: tuple[str, ...] = ()
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def down(self) -> bool:
        return self.ready and bool(getattr(self._inner, "down", False))

    def set(self, inner) -> None:
        with self._lock:
            inner.set_glossary(self._glossary)
            self._inner = inner
        self._ready.set()

    def set_glossary(self, terms) -> None:
        with self._lock:
            self._glossary = tuple(terms)
            if self._inner is not None:
                self._inner.set_glossary(self._glossary)

    def detect(self, pcm: bytes) -> dict[str, float] | None:
        self._ready.wait()
        return self._inner.detect(pcm)

    def transcribe(self, pcm: bytes, lang: str) -> str:
        self._ready.wait()
        return self._inner.transcribe(pcm, lang)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if self._inner is not None:
            self._inner.stop()


class ServerTranscriber:
    def __init__(self, servers: dict, client_factory: Callable[[str], WhisperClient] = WhisperClient,
                 on_error: Callable[[str], None] = lambda message: None):
        self.servers = servers
        self.client_factory = client_factory
        self.on_error = on_error
        self._lost: set[str] = set()
        self.clients: dict = {}
        self._restarted: set[str] = set()
        self._lock = threading.Lock()
        self.prompt = ""

    def start(self) -> None:
        for server in self.servers.values():
            server.start()
        for key, server in self.servers.items():
            server.wait_ready()
            self.clients[key] = self.client_factory(server.url)

    def stop(self) -> None:
        for server in self.servers.values():
            server.stop()

    def detect(self, pcm: bytes) -> dict[str, float] | None:
        if "langid" not in self.servers or "langid" in self._lost:
            return None
        try:
            return self._call("langid", lambda c: c.detect(pcm))
        except TranscriptionDown as e:
            self._lost.add("langid")
            self.on_error(f"language detection stopped ({e}); the language no longer switches by itself")
            return None

    def set_glossary(self, terms) -> None:
        self.prompt = glossary_prompt(terms)

    def transcribe(self, pcm: bytes, lang: str) -> str:
        prompt = self.prompt
        return self._call(lang, lambda c: c.transcribe(pcm, lang, prompt=prompt))

    def _call(self, key: str, fn):
        while True:
            client = self.clients[key]
            try:
                return fn(client)
            except _ERRORS as e:
                self._recover(key, client, e)

    def _recover(self, key: str, client, error: Exception) -> None:
        server = self.servers[key]
        with self._lock:
            if self.clients[key] is not client:
                return
            if server.proc.poll() is None:
                raise error
            if key in self._restarted:
                raise TranscriptionDown(f"whisper-server {key} stopped twice") from error
            self._restarted.add(key)
            try:
                server.restart()
            except Exception as e:
                raise TranscriptionDown(f"whisper-server {key} could not restart: {e}") from e
            self.clients[key] = self.client_factory(server.url)


def default_servers(log_dir: Path, vad: bool = True,
                    on_note: Callable[..., None] | None = None) -> dict[str, WhisperServer]:
    binary = tool("whisper-server")
    vad_model = model_path("vad") if vad else None
    common = dict(binary=binary, log_dir=log_dir, run_dir=RUN_DIR)
    if on_note:
        common["on_note"] = on_note
    return {
        "he": WhisperServer("he", model_path("he"), lang="he", vad_model=vad_model, **common),
        "en": WhisperServer("en", model_path("en"), lang="en", vad_model=vad_model, **common),
        "langid": WhisperServer("langid", model_path("langid"), threads=2, **common),
    }
