"""One engine process for the app: warm up while the call records, answer account_check, run one call,
summarize."""
from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable

from callcoach import __version__
from callcoach.cards import PREP_SCHEMA, parse_prep
from callcoach.config import CARD_GAP_S, GLOSSARY_PROMPT, RATE, RUN_DIR
from callcoach.context import (CallType, Goal, Prep, address_of, build_prefix, ensure_data, load_call_type,
                               prep_message, read_brief_terms, read_briefs)
from callcoach.cpu import CpuMeter
from callcoach.export import export_and_mark, export_root
from callcoach.ignore import load_ignored
from callcoach.knowledge import Entry, Knowledge, load_knowledge
from callcoach.meter import Meter
from callcoach.orchestrator import Orchestrator
from callcoach.prompts import PREP_RULES
from callcoach.protocol import PROTOCOL, Emitter, ProtocolError, parse_command
from callcoach.providers import ProviderAuth, drain_notes
from callcoach.store import call_folder, update_meta
from callcoach.summary import summarize, usage_section, write_terms
from callcoach.transcriber import WarmingTranscriber
from callcoach.usage import EMPTY, CallUsage, load_prices, totals


def template_goals(call_type: CallType) -> list[Goal]:
    return [Goal(f"g{i}", text) for i, text in enumerate(call_type.goals, 1)]


def with_terms(glossary: tuple[str, ...], terms) -> tuple[str, ...]:
    """The glossary with the names of the brief's terms after it, each name once."""
    names = list(glossary)
    seen = {name.casefold() for name in names}
    for term, _ in terms:
        if term.casefold() not in seen:
            seen.add(term.casefold())
            names.append(term)
    return tuple(names)


# The summary_ready message of a call that asked for no summary: a stop with the summary off, or a call that ended
# without a stop.
NO_SUMMARY = "בלי סיכום"


class CallAgent:
    """The agent side of one call: orchestrator, in-call commands, goals, usage and the summary. The coach is on or
    off and can switch at any moment; while it is off nothing is sent to the model. The summary is written only when the
    stop command asks for it; a call that ends without one (the end of stdin, auto stop, an error) has none, and it can
    be written later with `callcoach summary`. The first moment the coach
    is on with the ask section open (the start, the coach joining, or ask opening later) brings the brief's opening
    questions and, for a call without goals but with notes or a brief, a background request for goals and opening
    questions; once in a call, whatever is toggled after. The call starts with the call type's goals. The terms of the
    brief show as soon as a line says them."""

    def __init__(self, *, provider, emitter: Emitter, data: Path, prep: Prep, call_type: CallType,
                 usage: CallUsage, prices: dict, auth_error: str = "", today: Callable[[], date] = date.today,
                 clock: Callable[[], float] = time.time, brief_terms: tuple[tuple[str, str], ...] = (),
                 environ=os.environ, cpu: CpuMeter | None = None):
        self.provider = None if auth_error else provider
        self.environ = environ
        self.cpu = cpu or CpuMeter()
        self.emitter = emitter
        self.data = data
        self.prep = prep
        self.call_type = call_type
        self.usage = usage
        self.prices = prices
        self.auth_error = auth_error
        self.today = today
        self.clock = clock
        self.brief_terms = brief_terms
        self.orchestrator: Orchestrator | None = None
        self.coach = not prep.record_only
        self.ever_coach = self.coach
        # Whether the opening questions and the goals request had their moment (the coach on with ask open).
        self.opening_done = False
        self.summary = False
        self.folder: Path | None = None
        self.started = 0.0

    def begin(self, started: float, folder: Path) -> None:
        self.started, self.folder = started, folder
        goals = self.prep.goals or tuple(template_goals(self.call_type))
        known = load_knowledge(self.data)
        ignored = load_ignored(self.data)
        self.emitter.note("knowledge", files=list(known.files), entries=len(known.entries), skipped=known.skipped,
                          ambiguous=list(known.ambiguous), failed=list(known.failed), brief_terms=len(self.brief_terms),
                          ignored=len(ignored))
        self.orchestrator = Orchestrator(
            provider=self.provider, emitter=self.emitter, system=build_prefix(self.data, self.prep, self.call_type),
            coach=self.coach, call_type=self.call_type, started=started, goals=goals, level=self.prep.level,
            lang=self.prep.first_lang, length_min=self.prep.length_min, meter=Meter(), on_usage=self.usage.add,
            on_feature=self.usage.add_feature, clock=self.clock, card_gap_s=CARD_GAP_S, knowledge=known,
            brief_terms=Knowledge([Entry(term, "term", note) for term, note in self.brief_terms]),
            questions=self.prep.questions, title=self.prep.title, call_notes=self.prep.text, ignored=ignored,
            sections=self.prep.sections, list_only=self.prep.list_only)
        self.opening_done = self.coach and self.orchestrator.sections["ask"]
        if self.provider is not None:
            self.orchestrator.start()
        elif self.opening_done:
            self.orchestrator.first_questions()
        if self.opening_done and self._wants_goals():
            threading.Thread(target=self._make_goals, name="goals", daemon=True).start()

    def _wants_goals(self) -> bool:
        if self.prep.goals or self.provider is None:
            return False
        return bool(self.prep.text.strip()) or any(text for _, text in read_briefs(self.data, [self.prep.brief]))

    def _join(self) -> None:
        """A call that did not start with the coach on and ask open gets the opening questions and the goals request
        at the first moment it has both (the coach joining with ask open, or ask opening while the coach is on), and
        never again in the call."""
        if self.opening_done or not self.coach or not self.orchestrator.sections["ask"]:
            return
        self.opening_done = True
        self.orchestrator.first_questions()
        if self._wants_goals():
            threading.Thread(target=self._make_goals, name="goals", daemon=True).start()

    def set_coach(self, on: bool) -> None:
        """The coach switched on or off during the call. With no working account it stays off and the app hears
        why; the recording goes on either way. The first time it turns on is written to meta.json."""
        if on and self.provider is None:
            self.emitter.error("no_agent_account", self.auth_error or "no agent account is chosen")
            return
        self.coach = on
        self.orchestrator.set_coach(on)
        if on and not self.ever_coach:
            self.ever_coach = True
            self._meta(coach_from=round(self.clock() - self.started, 1))
        self._join()

    def _meta(self, **fields) -> None:
        try:
            update_meta(self.folder, **fields)
        except OSError as e:
            self.emitter.note("meta_failed", error=f"{type(e).__name__}: {e}")

    def _make_goals(self) -> None:
        """A failed request leaves the call type's goals; it goes to the log only. The new goals have ids of their
        own (b1, b2, ...), so a reply still about the first goals (g1, g2, ...) marks none of them. Its questions
        show only when no questions were shown yet."""
        try:
            result = self.provider.prepare(PREP_RULES, prep_message(self.data, self.prep, self.call_type),
                                           PREP_SCHEMA)
            self.usage.add(result.model, result.usage)
            reply = parse_prep(result.data)
        except Exception as e:
            self.emitter.note("goals_failed", error=type(e).__name__, message=str(e)[:200])
            return
        finally:
            drain_notes(self.provider, self.emitter)
        if reply.goals:
            self.orchestrator.set_goals([Goal(f"b{i}", text) for i, text in enumerate(reply.goals, 1)])
        self.orchestrator.offer_questions(reply.questions)

    def status(self) -> dict:
        state = self.orchestrator.status() if self.orchestrator else {"agent": "off", "level": self.prep.level,
                                                                        "visible": True}
        state = {**state, "agent": "auth"} if self.auth_error else state
        # What the panel's usage line and its "?" window show: tokens by feature, the engine's CPU, the limit.
        return {**state, **self.usage.live(), "cpu": self.cpu.value(),
                "limit": getattr(self.provider, "limit", "ok")}

    def hooks(self, folder: Path) -> dict:
        return {"on_segment": lambda seg: self.orchestrator.on_segment(seg),
                "on_line": lambda line, lang: self.orchestrator.on_line(line, lang),
                "on_partial": lambda channel, text: self.orchestrator.on_partial(text),
                "partials_wanted": lambda: bool(self.orchestrator and self.orchestrator.wants_partials()),
                "on_stopping": lambda: self.orchestrator.close(),
                "on_finished": lambda result: self.finish(folder),
                "status_extra": self.status}

    def command(self, engine, cmd: dict) -> bool:
        kind = cmd["type"]
        if kind == "stop":
            self.summary = cmd["summary"]
            engine.request_stop()
        elif kind == "set_coach":
            self.set_coach(cmd["on"])
        elif kind == "set_goal":
            self.orchestrator.set_goal(cmd["id"], cmd["done"])
        elif kind == "focus_goal":
            self.orchestrator.focus_goal(cmd["id"])
        elif kind == "set_language" and cmd["lang"] == "auto":
            engine.unlock_language()
        elif kind == "set_language":
            engine.lock_language(cmd["lang"])
        elif kind == "action" and cmd["id"] == "mark":
            engine.mark()
        elif kind == "action" and cmd["id"] == "terms_now":
            self.orchestrator.terms_now()
        elif kind == "action":
            self.orchestrator.ask_now(self.clock())
        elif kind == "set_visibility":
            self.orchestrator.set_visibility(cmd["visible"])
        elif kind == "set_level":
            self.orchestrator.set_level(cmd["level"])
        elif kind == "shown":
            self.orchestrator.shown(cmd["card_id"], cmd["t"])
        elif kind == "add_context":
            self.orchestrator.add_context(cmd["text"])
            self.emitter.note("context_added", chars=len(cmd["text"]))
        elif kind == "check_terms":
            self.orchestrator.check_terms(cmd["terms"])
        elif kind == "add_screen":
            self.orchestrator.add_screen(cmd["path"], cmd["text"])
            self.emitter.note("screen_added", chars=len(cmd["text"]))
        elif kind == "explain_term":
            self.orchestrator.explain_term(cmd["id"], more=cmd.get("more", False))
        elif kind == "set_hearing":
            engine.set_hearing(cmd["on"])
            self.orchestrator.set_hearing(cmd["on"])
        elif kind == "set_paused":
            engine.set_paused(cmd["on"])
            self.orchestrator.set_paused(cmd["on"])
        elif kind == "set_sections":
            self.orchestrator.set_sections(cmd["ask"], cmd["terms"], cmd.get("now", False), cmd.get("list_only", False))
            self._join()
        elif kind == "set_translate":
            self.orchestrator.set_translate(cmd["on"])
        elif kind == "recap":
            self.orchestrator.recap(since=bool(cmd.get("since")))
        else:
            return False
        return True

    def finish(self, folder: Path) -> None:
        goals = self.orchestrator.goal_list()
        self.emitter.emit("goals", goals=[g.to_dict() for g in goals])
        terms = self.orchestrator.term_list()
        self._write_terms(folder, terms)
        call = dict(EMPTY)

        def usage_text() -> str:
            call.update(self.usage.save(folder / "usage.json", self.prices))
            return usage_section(call)

        notes = "\n".join(filter(None, [self.prep.text.strip(), *self.orchestrator.context_notes()]))
        self._meta(goals=[g.to_dict() for g in goals], **({"notes": notes} if notes else {}))
        if not self.summary:
            call.update(self.usage.save(folder / "usage.json", self.prices))
            day, month = totals(folder.parent, self.today())
            self.emitter.emit("usage", call=call, today=day, month=month)
            self._export(folder)
            self.emitter.emit("summary_ready", ok=False, path=str(folder / "summary.md"), message=NO_SUMMARY)
            return
        prep = dataclasses.replace(self.prep, text=notes)
        result = summarize(self.provider, folder, prep, goals, on_usage=self.usage.add, usage_text=usage_text,
                           address=address_of(self.data), terms=terms)
        try:
            update_meta(folder, summary_kind=result.kind, summary_local=not result.ok)
        except OSError as e:
            self.emitter.note("meta_failed", error=f"{type(e).__name__}: {e}")
        if not result.ok and self.provider is not None:
            self.emitter.error("summary_failed", result.message)
        self._export(folder)
        day, month = totals(folder.parent, self.today())
        self.emitter.emit("usage", call=call, today=day, month=month)
        self.emitter.emit("summary_ready", ok=result.ok, path=str(result.path), message=result.message)

    def _export(self, folder: Path) -> None:
        """The copy for the export folder chosen in the app, if there is one; a copy that fails is reported and the
        call still ends as it would."""
        error = export_and_mark(folder, export_root(self.environ))
        if error:
            self.emitter.error("export_failed", error)

    def _write_terms(self, folder: Path, terms: list[dict]) -> None:
        """terms.json is written before the summary request, so a summary that fails still leaves it; a file that
        cannot be written goes to the log and the summary still comes."""
        try:
            write_terms(folder, terms)
        except OSError as e:
            self.emitter.note("terms_failed", error=f"{type(e).__name__}: {e}")


def vad_factory(mode: int = 2) -> Callable[[], Callable[[bytes], bool]]:
    import webrtcvad

    def new_vad():
        vad = webrtcvad.Vad(mode)
        return lambda frame: vad.is_speech(frame, RATE)

    return new_vad


def live_sources(prep: Prep, emitter: Emitter, log_dir: Path | None = None, *, mic_mode: str = "auto",
                 fast: bool = False):
    """The call's audio sources, the microphone always among them, and the audio echo check switch (on unless the
    helper cancels the echo). The live mic goes through CallCoach's own echo canceller, unless Apple's runs."""
    from callcoach.aec import cancel_echo
    from callcoach.sources import FileSource, MicSource, SystemSource, echo_cancel_wanted

    if prep.replay:
        return [FileSource(Path(prep.replay), realtime=not fast)], lambda: True
    on_error = lambda message: emitter.error("capture", message)
    mic = MicSource(on_error=on_error, on_info=lambda kind: emitter.note("mic", source=kind), mode=mic_mode,
                    log_dir=log_dir, echo_cancel=echo_cancel_wanted())
    system = SystemSource(on_error=on_error, on_dropout=lambda s: emitter.error(
        "system_audio_dropout", f"the other side's audio dropped for {s:.1f} s (output device change?)"))
    sources = [mic, system] if mic.echo_cancel else cancel_echo([mic, system], emitter.note)
    return sources, lambda: not mic.cancels_echo


def live_transcriber(emitter: Emitter, vad: bool = True):
    from callcoach.transcriber import ServerTranscriber, default_servers

    def make(log_dir: Path):
        return ServerTranscriber(default_servers(log_dir, vad=vad, on_note=emitter.note),
                                 on_error=lambda message: emitter.error("langid_stopped", message))

    return make


class Session:
    def __init__(self, *, data: Path, emitter: Emitter, account, provider, account_error: str = "",
                 make_transcriber: Callable, make_sources: Callable, make_engine: Callable, stdin=sys.stdin,
                 run_dir: Path = RUN_DIR, prices: dict | None = None, today: Callable[[], date] = date.today,
                 clock: Callable[[], float] = time.time, on_call_start: Callable = lambda engine: None,
                 after_call: Callable[[Path], None] = lambda folder: None, environ=os.environ):
        self.data = data
        self.environ = environ
        self.emitter = emitter
        self.account = account
        self.provider = provider
        self.account_error = account_error
        self.make_transcriber = make_transcriber
        self.make_sources = make_sources
        self.make_engine = make_engine
        self.stdin = stdin
        self.run_folder = run_dir / f"serve-{os.getpid()}"
        self.prices = load_prices() if prices is None else prices
        self.today = today
        self.clock = clock
        self.on_call_start = on_call_start
        self.after_call = after_call
        self.usage = CallUsage()
        self.transcriber = WarmingTranscriber()
        self.in_call = False
        self._call_lock = threading.Lock()
        self.lang = "he"
        self.level = "normal"
        self.agent_state = "auth" if account_error else ("off" if provider is None else "ok")

    def run(self) -> int:
        self.run_folder.mkdir(parents=True, exist_ok=True)
        self.emitter.set_log(self.run_folder / "events.log")
        self.emitter.emit("hello", protocol=PROTOCOL, version=__version__)
        ensure_data(self.data)
        self._status("warming")
        day, month = totals(self.data / "calls", self.today())
        self.emitter.emit("usage", call=dict(EMPTY), today=day, month=month)
        warm = threading.Thread(target=self._warm, name="warm", daemon=True)
        warm.start()
        try:
            start = self._before_call()
            if start:
                self._call(start)
            warm.join()
        except KeyboardInterrupt:
            warm.join()
        finally:
            self.transcriber.stop()
            if self.provider is not None:
                try:
                    self.provider.close()
                except Exception as e:
                    self.emitter.note("provider_close_failed", error=str(e))
                drain_notes(self.provider, self.emitter)
            shutil.rmtree(self.run_folder, ignore_errors=True)
            self.emitter.emit("stopped")
        return 0

    def _status(self, state: str) -> None:
        self.emitter.emit("status", state=state, elapsed=0.0, talk_share=0.0, lang_me=self.lang,
                          lang_them=self.lang, conversation_lang=self.lang, agent=self.agent_state,
                          level=self.level, visible=True, hearing=True, me_voice=False, paused=False,
                          transcribing=self.transcriber.ready)

    def _warm(self) -> None:
        """Starts the whisper servers and hands them to the stand-in, the servers or a down transcriber, whatever
        happens. The idle status follows only when no call has started."""
        from callcoach.transcriber import DownTranscriber

        transcriber = None
        try:
            try:
                transcriber = self.make_transcriber(self.run_folder / "logs")
                transcriber.start()
            except Exception as e:
                if transcriber is not None:
                    transcriber.stop()
                self.emitter.error("transcription_start_failed", str(e) or type(e).__name__)
                transcriber = DownTranscriber(f"transcription is off for this call ({e})")
        finally:
            self.transcriber.set(transcriber or DownTranscriber("transcription did not start"))
        with self._call_lock:
            if not self.in_call:
                self._status("idle")

    def _commands(self):
        for raw in iter(self.stdin.readline, ""):
            if not raw.strip():
                continue
            try:
                yield parse_command(raw)
            except ProtocolError as e:
                self.emitter.error("bad_command", str(e))

    def _before_call(self) -> dict | None:
        for cmd in self._commands():
            if cmd["type"] == "start":
                return cmd
            if cmd["type"] == "account_check":
                self.account_check()
            else:
                self.emitter.error("bad_command", f"{cmd['type']} needs a call in progress")
        return None

    def account_check(self) -> None:
        ok, message = False, self.account_error or "no agent account is chosen"
        if self.provider is not None and not self.account_error:
            try:
                result = self.provider.check()
                self.usage.add(result.model, result.usage)
                ok, message, self.agent_state = True, "", "ok"
            except ProviderAuth as e:
                message, self.agent_state = str(e) or "the account was rejected", "auth"
            except Exception as e:
                message, self.agent_state = str(e) or type(e).__name__, "unavailable"
        self.emitter.emit("account", ok=ok, provider=self.account.provider, name=self.account.name,
                          via_claude_code=self.account.via_claude_code, message=message)

    def _move_log(self, folder: Path) -> None:
        """The events before the call go to the top of the call's own events.log."""
        self.emitter.set_log(None)
        error = None
        try:
            shutil.move(self.run_folder / "events.log", folder / "events.log")
        except OSError as e:
            error = e
        self.emitter.set_log(folder / "events.log")
        if error:
            self.emitter.note("log_move_failed", error=type(error).__name__)

    def _call(self, cmd: dict) -> None:
        with self._call_lock:
            self.in_call = True
        prep = Prep.from_command(cmd)
        terms = read_brief_terms(self.data, prep.brief)
        prep = dataclasses.replace(prep, glossary=with_terms(prep.glossary, terms))
        call_type = load_call_type(self.data, prep.call_type)
        folder = call_folder(self.data, prep.name)
        self._move_log(folder)
        if GLOSSARY_PROMPT:
            self.transcriber.set_glossary(prep.glossary)
        sources, echo_check = self.make_sources(prep, self.emitter, folder / "logs")
        agent = CallAgent(provider=self.provider, emitter=self.emitter, data=self.data, environ=self.environ,
                          prep=prep, call_type=call_type, usage=self.usage, prices=self.prices,
                          auth_error=self.account_error,
                          today=self.today, clock=self.clock, brief_terms=terms)
        hooks = agent.hooks(folder)
        finish = hooks["on_finished"]

        def on_finished(result) -> None:
            if (self.run_folder / "logs").is_dir():
                shutil.copytree(self.run_folder / "logs", folder / "logs", dirs_exist_ok=True)
            finish(result)
            if result.ok:
                try:
                    self.after_call(folder)
                except Exception as e:
                    self.emitter.note("after_call_failed", error=f"{type(e).__name__}: {e}"[:300])

        hooks["on_finished"] = on_finished
        engine = self.make_engine(folder=folder, sources=sources, transcriber=self.transcriber, emitter=self.emitter,
                                  start_lang=prep.first_lang, glossary=prep.glossary, echo_check=echo_check, **hooks)
        self._write_meta(folder, prep)
        agent.begin(engine.started, folder)
        self.on_call_start(engine)
        threading.Thread(target=self._in_call, args=(engine, agent), name="commands", daemon=True).start()
        engine.run()

    def _write_meta(self, folder: Path, prep: Prep) -> None:
        """meta.json gets the call's title, the brief, its names and terms (for the final pass) and the call type,
        whether it started as a recording, and coach_from 0 for a call that starts with the coach, beside the start
        time the engine wrote."""
        fields = {key: value for key, value in (("title", prep.title), ("brief", prep.brief),
                                                ("record_only", prep.record_only), ("glossary", list(prep.glossary)))
                  if value}
        fields["call_type"] = prep.call_type
        if not prep.record_only:
            fields["coach_from"] = 0
        try:
            update_meta(folder, **fields)
        except OSError as e:
            self.emitter.note("meta_failed", error=f"{type(e).__name__}: {e}")

    def _in_call(self, engine, agent: CallAgent) -> None:
        for cmd in self._commands():
            # One failing command must not end this loop: after it, stop and the end of stdin would go unseen.
            try:
                allowed = agent.command(engine, cmd)
            except Exception as e:
                self.emitter.error("command_failed", f"{cmd['type']} failed: {type(e).__name__}: {e}"[:300])
                continue
            if not allowed:
                self.emitter.error("bad_command", f"{cmd['type']} is not allowed during a call")
        engine.request_stop()
