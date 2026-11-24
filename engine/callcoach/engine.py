"""Wires sources, recording, segmentation, language routing, transcription and events.

While CallCoach does not hear me (set_hearing), every microphone chunk is silence of the same length before anything
uses it: me.wav stays in step with them.wav, and nothing of my voice is kept, transcribed, sent on or measured."""
from __future__ import annotations

import dataclasses
import itertools
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from callcoach.data import CHANNELS, ME, THEM, Chunk, Line, Segment
from callcoach.echo_audio import AudioHistory, echo_score
from callcoach.language import ConversationLanguage, LanguageRouter
from callcoach.protocol import Emitter
from callcoach.recorder import FinalizeResult, Recorder
from callcoach.segmenter import Segmenter
from callcoach.store import TranscriptStore
from callcoach.textfilter import EchoFilter, clean
from callcoach.transcriber import Transcriber, TranscriptionDown


STATUS_DEFAULTS = {"agent": "off", "level": "normal", "visible": True}
ME_VOICE_S = 1.0


class CallEngine:
    def __init__(self, *, folder: Path, sources: list, transcriber: Transcriber, emitter: Emitter,
                 new_vad: Callable[[], Callable[[bytes], bool]], start_lang: str = "he",
                 clock: Callable[[], float] = time.time, workers: int = 4,
                 auto_stop_seconds: float = 300.0, status_every: float = 1.0,
                 status_extra: Callable[[], dict] | None = None, echo_threshold: float | None = 0.65,
                 echo_check: Callable[[], bool] = lambda: True, glossary: tuple[str, ...] = (),
                 on_segment: Callable[[Segment], None] | None = None,
                 on_line: Callable[[Line, str], None] | None = None,
                 on_partial: Callable[[str, str], None] | None = None,
                 partials_wanted: Callable[[], bool] = lambda: True, partial_every: float = 2.0,
                 partial_window: float = 6.0,
                 on_stopping: Callable[[], None] | None = None,
                 on_finished: Callable[[FinalizeResult], None] | None = None):
        self.folder = folder
        self.sources = sources
        self.transcriber = transcriber
        self.emitter = emitter
        self.clock = clock
        self.auto_stop_seconds = auto_stop_seconds
        self.status_every = status_every
        self.status_extra = status_extra
        self.started = clock()
        self.recorder = Recorder(folder, self.started)
        self.store = TranscriptStore(folder, self.started)
        ids = itertools.count(1)
        self.segmenters = {ch: Segmenter(ch, new_vad(), ids) for ch in CHANNELS}
        self.routers = {ch: LanguageRouter(start_lang) for ch in CHANNELS}
        self.conversation = ConversationLanguage(start_lang)
        self.echo = EchoFilter()
        self.echo_threshold = echo_threshold
        self.echo_check = echo_check
        self.them_audio = AudioHistory()
        self.glossary = glossary
        self.on_segment = on_segment
        self.on_line = on_line
        self.on_partial = on_partial
        self.partials_wanted = partials_wanted
        self.partial_every = partial_every
        self.partial_window = partial_window
        self._partial_at: tuple[float, float] = (-1.0, 0.0)
        self._partial_busy = False
        self._partial_ready = 0.0
        self._stopping = False
        self.on_stopping = on_stopping
        self.on_finished = on_finished
        self.chunks: queue.Queue[Chunk] = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.voiced = {ME: 0.0, THEM: 0.0}
        self.last_voice = self.started
        self.last_t = {ch: self.started for ch in CHANNELS}
        self._line_ids = itertools.count(1)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._closing = False
        self._down = bool(getattr(transcriber, "down", False))
        self._detected: set[int] = set()
        self._commits: dict[int, tuple[str, str]] = {}
        self._store_failed = False
        self.recording = True
        self.hearing = True
        self._offs = 0
        self._offs_applied = 0
        self.paused = False
        self._pauses = 0
        self._pauses_applied = 0

    def request_stop(self) -> None:
        self._stop.set()

    def lock_language(self, lang: str) -> None:
        for router in self.routers.values():
            router.lock(lang)
        self.conversation.lock(lang)

    def unlock_language(self) -> None:
        for router in self.routers.values():
            router.unlock()
        self.conversation.unlock()

    def set_hearing(self, on: bool) -> None:
        """Whether CallCoach hears me. Each change is a note in the transcript; the sentence of mine still open when
        it stops hearing is dropped before my next chunk (by the loop, which owns the segmenters)."""
        with self._lock:
            if on == self.hearing or self._closing:
                return
            self.hearing = on
            if not on:
                self._offs += 1
            self._save(lambda: self.store.hearing(on, self.clock()))
        self.emitter.note("hearing", on=on)

    def set_paused(self, on: bool) -> None:
        """While the recording is paused, both sides are silence of the same length before anything else, so
        nothing is recorded, transcribed or sent, and the call does not end for the silence. Each change is a note in
        the transcript; the sentences still open when it pauses are dropped (by the loop, which owns the
        segmenters), and the 5 minutes with no speech count again from the moment it goes on."""
        with self._lock:
            if on == self.paused or self._closing:
                return
            self.paused = on
            if on:
                self._pauses += 1
            else:
                self.last_voice = max(self.last_voice, self.clock())
            self._save(lambda: self.store.paused(on, self.clock()))
        self.emitter.note("paused", on=on)

    def _is_paused(self) -> bool:
        """Whether the next chunk is paused. When a pause began since the last one, the open sentences are dropped."""
        pauses = self._pauses
        if pauses != self._pauses_applied:
            self._pauses_applied = pauses
            for segmenter in self.segmenters.values():
                segmenter.discard()
        return self.paused

    def _hear_me(self) -> bool:
        """Whether my next chunk is heard. When hearing went off since the last one, even if it is on again, the
        sentence of mine still open is dropped."""
        offs = self._offs
        if offs != self._offs_applied:
            self._offs_applied = offs
            self.segmenters[ME].discard()
        return self.hearing

    def _me_voice(self) -> bool:
        """CallCoach heard my voice in the last second; never while it does not hear me."""
        heard = self.segmenters[ME].heard_at
        return self.hearing and not self.paused and heard is not None and self.clock() - heard <= ME_VOICE_S

    def mark(self) -> None:
        with self._lock:
            if not self._closing:
                self._save(lambda: self.store.bookmark(self.clock()))

    def run(self) -> FinalizeResult:
        started = []
        for source in self.sources:
            try:
                source.start(self.chunks.put)
                started.append(source)
            except Exception as e:
                self.emitter.error("source", f"{type(source).__name__} could not start: {e}")
        self.sources = started
        if not started:
            self.emitter.error("no_sources", "no audio source could start; the call ends")
        replay_t0 = getattr(started[0], "t0", None) if started else None
        self.emitter.emit("ready", folder=str(self.folder), replay_t0=replay_t0)
        try:
            if started:
                self._loop()
        except Exception as e:
            self.emitter.error("engine", f"engine loop failed: {e}")
        return self._shutdown()

    def _loop(self) -> None:
        next_status = self.clock()
        while not self._stop.is_set():
            try:
                self._handle_chunk(self.chunks.get(timeout=0.1))
            except queue.Empty:
                pass
            now = self.clock()
            with self._lock:
                self._publish(self.echo.tick(now))
            if now >= next_status:
                for source in self.sources:
                    check = getattr(source, "check", None)
                    if check:
                        check(now)
                self._status("active")
                next_status = now + self.status_every
            if self._sources_done() and self.chunks.empty():
                return
            if not self.paused and now - self.last_voice > self.auto_stop_seconds:
                self.emitter.error("auto_stop", f"no speech for {self.auto_stop_seconds:.0f} s, call ended")
                return

    def _sources_done(self) -> bool:
        flags = [getattr(s, "done", None) for s in self.sources]
        return bool(flags) and all(f is not None and f.is_set() for f in flags)

    def _handle_chunk(self, chunk: Chunk) -> None:
        heard = not self._is_paused() and (chunk.channel == THEM or self._hear_me())
        if not heard:
            chunk = dataclasses.replace(chunk, pcm=bytes(len(chunk.pcm)))
        if self.recording:
            try:
                self.recorder.write(chunk)
            except OSError as e:
                self.recording = False
                self.emitter.error("recording_stopped", f"recording stopped ({e}); transcription continues")
        self.last_t[chunk.channel] = chunk.t_end
        if heard:
            for seg in self.segmenters[chunk.channel].feed(chunk.pcm, chunk.t_end):
                self._segment(seg)
        if chunk.channel == THEM:
            self.them_audio.add(chunk.pcm, chunk.t_end)
            with self._lock:
                self.echo.advance(chunk.t_end)
            if heard:
                self._maybe_partial()

    def _maybe_partial(self) -> None:
        """While the other side is still saying a sentence, transcribe it again every partial_every seconds of
        its speech (its last partial_window seconds), so a name in it is found before the sentence ends. A short
        sentence gets none: its line comes soon anyway. The next one waits as long as the last one took, so they
        never take more than half of the server's time."""
        if not getattr(self.transcriber, "ready", True):
            return
        segmenter = self.segmenters[THEM]
        if self.on_partial is None or self._down or self._stopping or not segmenter.active:
            return
        start, done = self._partial_at
        if segmenter.t_start != start:
            start, done = segmenter.t_start, 0.0
        length = segmenter.spoken_seconds
        if (length - done < self.partial_every or self._partial_busy or self.clock() < self._partial_ready
                or not self.partials_wanted()):
            self._partial_at = (start, done)
            return
        self._partial_at = (start, length)
        self._partial_busy = True
        self.pool.submit(self._partial, segmenter.open_audio(self.partial_window), self.routers[THEM].current,
                         length, start)

    def _partial(self, pcm: bytes, lang: str, length: float, t_start: float) -> None:
        """The app shows the text as the sentence that starts at t_start, until its line comes."""
        began = self.clock()
        try:
            text = clean(self.transcriber.transcribe(pcm, lang), self.glossary)
        except TranscriptionDown as e:
            self._transcription_down(e)
            return
        except Exception as e:
            self.emitter.note("partial_failed", error=f"{type(e).__name__}: {e}")
            return
        finally:
            took = self.clock() - began
            self._partial_ready = self.clock() + took
            self._partial_busy = False
        self.emitter.note("partial", seconds=round(length, 2), took=round(took, 3))
        if text:
            self.emitter.emit("partial", channel=THEM, text=text, t_start=t_start)
            self._hook("on_partial", THEM, text)

    def _hook(self, name: str, *args) -> None:
        fn = getattr(self, name)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception as e:
            self.emitter.note("hook_failed", hook=name, error=f"{type(e).__name__}: {e}")

    def _segment(self, seg: Segment) -> None:
        """A closed segment is transcribed. My share of the talk counts only the time CallCoach hears me: while it
        does not, neither side is counted or measured."""
        if self.hearing:
            self.voiced[seg.channel] += seg.duration
        self.last_voice = max(self.last_voice, seg.speech_end)
        self.emitter.emit("segment", id=seg.id, channel=seg.channel, t_start=seg.t_start,
                          speech_end=seg.speech_end, closed_at=seg.closed_at, duration=round(seg.duration, 3))
        if self.hearing:
            self._hook("on_segment", seg)
        with self._lock:
            self.echo.expect(seg)
        self.pool.submit(self._transcribe, seg)

    def _audio_echo(self, seg: Segment) -> bool:
        if seg.channel != ME or self.echo_threshold is None or self.them_audio.end is None:
            return False
        if not self.echo_check():
            return False
        end = seg.t_start + seg.duration
        deadline = time.monotonic() + 1.0
        while self.last_t[THEM] < end + 0.3 and time.monotonic() < deadline and not self._sources_done():
            time.sleep(0.02)
        score = echo_score(seg.pcm, self.them_audio.slice(seg.t_start, end))
        if score < self.echo_threshold:
            return False
        self.emitter.note("echo_dropped", segment_id=seg.id, score=round(score, 3))
        return True

    def _echo_dropped(self, seg: Segment) -> bool:
        """The audio echo check fails open: a check that raises lets the segment be transcribed."""
        try:
            return self._audio_echo(seg)
        except Exception as e:
            self.emitter.note("echo_check_failed", segment_id=seg.id, error=f"{type(e).__name__}: {e}")
            return False

    def _transcribe(self, seg: Segment) -> None:
        if self._echo_dropped(seg):
            return
        text, lang, detected = None, "", False
        if not self._down:
            try:
                router = self.routers[seg.channel]
                probs = self._detect(seg) if router.needs_detection(seg.duration) else None
                lang, found = router.choose(seg.duration, probs)
                detected = found is not None
                text = clean(self.transcriber.transcribe(seg.pcm, lang), self.glossary)
                if text is None and detected and lang != router.current:
                    lang, detected = router.current, False
                    text = clean(self.transcriber.transcribe(seg.pcm, lang), self.glossary)
            except TranscriptionDown as e:
                self._transcription_down(e)
            except Exception as e:
                self.emitter.error("transcription_failed", str(e))
        with self._lock:
            if text is None:
                self.echo.done(seg.id)
                return
            line = Line(next(self._line_ids), seg.id, seg.channel, lang, text, seg.t_start,
                        seg.speech_end, self.clock())
            if detected:
                self._detected.add(line.id)
                self._commits[line.id] = (seg.channel, lang)
            self._publish(self.echo.add(line, self.clock()))

    def _detect(self, seg: Segment) -> dict[str, float] | None:
        try:
            return self.transcriber.detect(seg.pcm)
        except Exception as e:
            self.emitter.error("langid_failed", f"language detection failed ({e}); the line keeps the current language")
            return None

    def _transcription_down(self, error: Exception) -> None:
        """A transcriber that worked and then failed is reported once as transcription_stopped; one that was down
        from the start (its warm-up already reported transcription_start_failed) only goes to the log."""
        with self._lock:
            if self._down:
                return
            self._down = True
        if getattr(self.transcriber, "down", False):
            self.emitter.note("transcription_off", error=str(error))
            return
        self.emitter.error("transcription_stopped", f"{error}; the recording continues")

    def _publish(self, lines: list[Line]) -> None:
        for line in lines:
            commit = self._commits.pop(line.id, None)
            if commit:
                self.routers[commit[0]].commit(commit[1])
            line = dataclasses.replace(line, line_ready=self.clock())
            self._save(lambda: self.store.add(line))
            counts = line.channel == THEM and line.id in self._detected
            conv = self.conversation.observe(line.lang) if counts else self.conversation.current
            self.emitter.emit("line", **line.to_dict(), conversation_lang=conv)
            self._hook("on_line", line, conv)

    def _save(self, write: Callable[[], object]) -> None:
        try:
            write()
        except OSError as e:
            if not self._store_failed:
                self._store_failed = True
                self.emitter.error("transcript_stopped", f"transcript file stopped ({e}); lines are still shown")

    def _status(self, state: str) -> None:
        total = self.voiced[ME] + self.voiced[THEM]
        extra = {**STATUS_DEFAULTS, **(self.status_extra() if self.status_extra else {})}
        self.emitter.emit("status", state=state, elapsed=round(self.clock() - self.started, 1),
                          talk_share=round(self.voiced[ME] / total, 2) if total else 0.0,
                          lang_me=self.routers[ME].current, lang_them=self.routers[THEM].current,
                          conversation_lang=self.conversation.current, hearing=self.hearing,
                          me_voice=self._me_voice(), paused=self.paused,
                          transcribing=bool(getattr(self.transcriber, "ready", True)), **extra)

    def _shutdown(self) -> FinalizeResult:
        self._stopping = True
        self._status("stopping")
        self._hook("on_stopping")
        for source in self.sources:
            try:
                source.stop()
            except Exception as e:
                self.emitter.error("source", f"{type(source).__name__} did not stop: {e}")
            if getattr(source, "error", None):
                self.emitter.error("source", str(source.error))
        try:
            while not self.chunks.empty():
                self._handle_chunk(self.chunks.get_nowait())
            self._hear_me()
            for ch, segmenter in self.segmenters.items():
                seg = segmenter.flush(self.last_t[ch])
                if seg:
                    self._segment(seg)
        except Exception as e:
            self.emitter.error("engine", f"the last audio could not be processed: {e}")
        try:
            result = self.recorder.finalize()
        except Exception as e:
            result = FinalizeResult(False, reason=f"finalize failed: {e}")
        self.emitter.emit("finalized", ok=result.ok, seconds=round(result.seconds, 1), reason=result.reason)
        self.pool.shutdown(wait=True)
        with self._lock:
            self._closing = True
            self._publish(self.echo.tick(float("inf")))
            self._save(self.store.render)
        self.recorder.release()
        self._hook("on_finished", result)
        self.emitter.emit("stopped")
        return result
