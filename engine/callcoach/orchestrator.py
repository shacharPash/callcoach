"""Turns transcript lines into agent requests, and replies into cards, terms, live questions, the question asked of
me, the line on what is discussed now and goal updates.

While the coach is off (a recording) nothing is sent to the model and no section is computed. The panel's sections
(ask, terms, and the now line in its recap) can each be closed: a closed section is not asked for and what still
comes for it is dropped; with all closed there is no live request. With "list only" the terms come from the knowledge
files and the brief alone: the live model is told terms are off, and with questions and the now line closed too there
is no live request, while a search the user asks for still goes to the model. While CallCoach does not hear me, the question asked of me is neither
shown nor asked for.

Names from the knowledge files and terms from the brief show as soon as a line names them, without waiting for the
agent. A term clicked in the panel, and the recap of the call so far, each have their own request beside the live
step."""
from __future__ import annotations

import dataclasses
import itertools
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from callcoach.cards import (MAX_SINCE_LINES, MAX_SINCE_WORDS, REPLY_SCHEMA, BadReply, Question, card_for_call, fold,
                             parse_detail, parse_recap, parse_reply, parse_screen, parse_spotted, parse_translations,
                             questions_for_call, text_lang)
from callcoach.context import CallType, Goal
from callcoach.data import ME, THEM, Line, Segment
from callcoach.knowledge import Entry, Knowledge, ours_line, ours_link
from callcoach.meter import Meter
from callcoach.prompts import (CHECK_RULES, EXPLAIN_MORE_RULES, EXPLAIN_RULES, RECAP_RULES, RECAP_SINCE_RULES,
                               SCREEN_RULES, SPOT_RULES, TRANSLATE_RULES)
from callcoach.protocol import ALL_SECTIONS, Emitter
from callcoach.providers import ProviderAuth, Usage, drain_notes

CARDS_IN_STATE = 8
QUESTIONS_HOLD_S = 45.0
TERMS_IN_STATE = 40
DETAIL_WINDOW_S = 180.0
RECAP_WINDOW_S = 1800.0
TERMS_NOW_WINDOW_S = 30.0
PARTIAL_FRESH_S = 8.0
# A shown now line stays this long before a newer one replaces it, so the panel does not keep changing under the eye.
NOW_HOLD_S = 45.0
SCREEN_WINDOW_S = 120.0
MAX_SCREEN_BYTES = 8_000_000
SCREEN_NOTE = "מהמסך: "
SCREEN_FAILED = "לא הצלחתי לקרוא את המסך"
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
DETAIL_FAILED = "לא הצלחתי להרחיב"
RECAP_FAILED = "לא הצלחתי לסכם"
COACH_OFF = "המאמן כבוי"
NOTHING_NEW = "אין חדש"
# An update reads from a few seconds before the last line the answers so far read.
RECAP_SINCE_OVERLAP_S = 5.0
NO_SUCH_TERM = "אין מונח כזה בשיחה"
CALL_ENDED = "השיחה כבר הסתיימה"
# A translation asks for the newest lines waiting, at most this many, and shows the lines said just before them.
MAX_TRANSLATE_LINES = 3
TRANSLATE_BEFORE_LINES = 3


@dataclass
class Request:
    mode: str
    lines: list[Line]
    trigger_ids: list[int]
    trigger_t: float
    forced: bool = False
    message: str = ""
    requested_at: float = 0.0


def _clock_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Orchestrator:
    def __init__(self, *, provider, emitter: Emitter, system: str, call_type: CallType, started: float,
                 goals=(), level: str = "normal", lang: str = "he", length_min: float | None = None,
                 meter: Meter | None = None, on_usage: Callable[[str, Usage], None] = lambda model, usage: None,
                 on_feature: Callable[[str, Usage], None] = lambda feature, usage: None,
                 clock: Callable[[], float] = time.time, stale_s: float = 8.0, retry_s: float = 15.0,
                 min_words: int = 3, poll_s: float = 0.25, card_gap_s: float = 0.0,
                 knowledge: Knowledge | None = None, brief_terms: Knowledge | None = None,
                 questions: tuple[Question, ...] = (), title: str = "", call_notes: str = "",
                 ignored: tuple[str, ...] = (), coach: bool = True,
                 sections: tuple[bool, ...] | None = None, list_only: bool = False):
        self.provider = provider
        self.emitter = emitter
        self.system = system
        self.call_type = call_type
        self.started = started
        self.goals: dict[str, Goal] = {g.id: g for g in goals}
        self.level = level
        self.lang = lang
        self.length_min = length_min
        self.meter = meter
        self.on_usage = on_usage
        self.on_feature = on_feature
        self.clock = clock
        self.stale_s = stale_s
        self.retry_s = retry_s
        self.min_words = min_words
        self.poll_s = poll_s
        self.card_gap_s = card_gap_s
        self.knowledge = knowledge or Knowledge()
        self.brief_terms = brief_terms or Knowledge()
        self.ignored_names = tuple(ignored)
        self.ignored = {fold(name) for name in ignored} | {
            fold(entry.name) for entry in map(self.knowledge.lookup, ignored) if entry is not None}
        self.ignored.discard("")
        self.title = title
        self.call_notes = call_notes.strip()
        self.last_card_at: float | None = None
        self.notes: list[str] = []
        self.state = "ok" if provider else "off"
        self.visible = True
        self.pending: list[Line] = []
        self.cards: dict[int, dict] = {}
        self.terms: dict[str, dict] = {}
        self.questions = tuple(questions)
        self.opening = tuple(questions)
        self.questions_at: float | None = None
        self.asked: Question | None = None
        self.coach = coach
        # Sections given without the now line (an older app) leave it off; none given open all of them with the coach.
        self.sections = (dict.fromkeys(ALL_SECTIONS, False) | dict(zip(ALL_SECTIONS, sections)) if sections
                         else dict.fromkeys(ALL_SECTIONS, coach))
        self.list_only = list_only
        self.now = ""
        self.now_at: float | None = None
        self.hearing_me = True
        self.hearing_log: list[tuple[float, bool]] = []
        self.pause_log: list[tuple[float, bool]] = []
        self.history: list[Line] = []
        self.explaining: set[int] = set()
        self.spotting = False
        self.partial: tuple[str, float] | None = None
        self.recapping = False
        self.translate_on = False
        self.to_translate: list[Line] = []
        self.translating = False
        self.recap_answer: tuple[int, str] | None = None
        # Every answer so far, the recap and then each update, and how far they read: the lines and the time.
        self.recap_said: list[str] = []
        self.recap_seen = 0
        self.recap_upto: float | None = None
        self.live_questions = "question" in call_type.cards
        self.forced_at: float | None = None
        self.forced_bypassed = False
        self.noted = False
        self.by_hand: set[str] = set()
        self.focus: str | None = None
        self.focus_asked = False
        self.catch_up = False
        self.retry_at = 0.0
        self.closed = False
        self._opened = False
        self._last_error: str | None = None
        self._ids = itertools.count(1)
        self.cond = threading.Condition()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.coach and self.sections["ask"]:
            self.first_questions()
        self.thread = threading.Thread(target=self._run, name="orchestrator", daemon=True)
        self.thread.start()

    def first_questions(self) -> None:
        """The opening questions of the brief show at once, until live questions replace them; without them a call
        with live questions and an agent starts with none. They are the brief's even after closing ask forgot what it
        showed, so a coach that joins later still gets them."""
        with self.cond:
            if self.opening:
                self._show_questions(self.opening, [])
            elif self.live_questions and self.provider is not None:
                self.emitter.emit("questions", items=[], lang=self.lang, trigger_line_ids=[])

    def close(self, timeout: float = 20.0) -> None:
        with self.cond:
            self.closed = True
            self.cond.notify_all()
        if self.thread:
            self.thread.join(timeout)

    def on_line(self, line: Line, conversation_lang: str) -> None:
        with self.cond:
            self.lang = conversation_lang
            if not self.closed:
                if line.channel == THEM:
                    self.partial = None
                if self.coach:
                    self.pending.append(line)
                self.history.append(line)
                self._find_names(line.text, [line.id])
                translate = self._queue_translation(line)
            else:
                translate = False
            self.cond.notify_all()
        if translate:
            threading.Thread(target=self._translate_loop, name="translate", daemon=True).start()

    def set_coach(self, on: bool) -> None:
        """The coach on or off. While it is off nothing is sent to the model, whatever the app asks: no live step,
        translation, recap, term or screen request. Lines said while it was off never reach the live step
        (the recap and the summary still read the whole call), and the sections stay as they were."""
        with self.cond:
            if on and not self.coach:
                self.pending, self.forced_at, self.catch_up, self.noted = [], None, False, False
            if not on:
                self.to_translate.clear()
            self.coach = on
            self.emitter.note("coach", on=on)
            self.cond.notify_all()

    def set_translate(self, on: bool) -> None:
        """English lines of the other side get a Hebrew translation while the panel shows the transcript. Off forgets
        the lines still waiting."""
        with self.cond:
            self.translate_on = on
            if not on:
                self.to_translate.clear()

    def _queue_translation(self, line: Line) -> bool:
        """Whether this line starts the translating thread: none runs, and the line is the other side's, in English,
        with translation on and an agent that works."""
        if not (self.coach and self.translate_on and line.channel == THEM and line.lang == "en"
                and self.provider is not None and self.state != "auth"):
            return False
        self.to_translate.append(line)
        if self.translating:
            return False
        self.translating = True
        return True

    def _translate_loop(self) -> None:
        """One request at a time: lines that come while one runs wait, and the next request takes the newest of
        them; older ones are no longer on the panel and are not translated."""
        while True:
            with self.cond:
                batch = self.to_translate[-MAX_TRANSLATE_LINES:]
                self.to_translate.clear()
                if not batch or self.closed or not self.translate_on or not self.coach:
                    self.translating = False
                    return
                message = self._translate_message(batch)
            found = self._translate(message, [l.id for l in batch])
            with self.cond:
                if self.translate_on and self.coach and not self.closed:
                    for line_id, he in found:
                        self.emitter.emit("translation", id=line_id, he=he)

    def _translate_message(self, batch: list[Line]) -> str:
        first = min(l.t_start for l in batch)
        asked = {l.id for l in batch}
        before = [l for l in sorted(self.history, key=lambda l: l.t_start) if l.t_start < first and l.id not in asked]
        rows = [self._row(l) for l in before[-TRANSLATE_BEFORE_LINES:]]
        return "\n".join(["## Before", *(rows or ["(none)"]), "## Translate",
                          *(f"{l.id}: {l.text}" for l in sorted(batch, key=lambda l: l.t_start))]) + "\n"

    def _translate(self, message: str, ids: list[int]) -> tuple[tuple[int, str], ...]:
        try:
            result = self.provider.translate(TRANSLATE_RULES, message)
            self.on_usage(result.model, result.usage)
            self.on_feature("translate", result.usage)
            found, problems = parse_translations(result.data, ids)
            for problem in problems:
                self.emitter.note("translation_problem", problem=problem)
            return found
        except Exception as e:
            self.emitter.note("translation_failed", error=type(e).__name__, message=str(e)[:200])
            return ()
        finally:
            drain_notes(self.provider, self.emitter)

    def on_partial(self, text: str) -> None:
        """A sentence the other side is still saying: only the known names and brief terms in it are used, at once;
        the agent gets the sentence when its line comes, and a search for what was just said gets it at once."""
        with self.cond:
            if not self.closed:
                self.partial = (text, self.clock())
                self._find_names(text, [])

    def _find_names(self, text: str, trigger_ids: list[int]) -> None:
        """The names from the knowledge files and the terms from the brief that a text says, while terms are open."""
        if not self.sections["terms"]:
            return
        for entry, said in self.knowledge.said_in(text):
            self._emit_entry(entry, trigger_ids, said)
        for entry, said in self.brief_terms.said_in(text):
            self._emit_brief_term(entry, trigger_ids, said)

    def wants_partials(self) -> bool:
        """Partial transcripts only find names early: they run while terms are open and there is a name to find."""
        with self.cond:
            return self.sections["terms"] and bool(self.knowledge.entries or self.brief_terms.entries)

    def on_segment(self, seg: Segment) -> None:
        if self.meter:
            with self.cond:
                self.meter.add(seg.channel, seg.duration, seg.speech_end)

    def ask_now(self, t: float) -> None:
        """A forced question; only while the coach is on and ask is open (the app opens it first)."""
        with self.cond:
            if not self.coach or not self.sections["ask"]:
                self.emitter.note("ask_now_closed")
                return
            self.forced_at = t
            self.forced_bypassed = False
            self.cond.notify_all()

    def set_visibility(self, visible: bool) -> None:
        with self.cond:
            if visible and not self.visible:
                self.catch_up = True
            self.visible = visible
            self.cond.notify_all()

    def model_sections(self) -> dict[str, bool]:
        """The sections the live model works on: terms are not its with list only, which the knowledge files and the
        brief alone fill."""
        return {**self.sections, "terms": self.sections["terms"] and not self.list_only}

    def set_sections(self, ask: bool, terms: bool, now: bool = False, list_only: bool = False) -> None:
        """Which sections are computed. Closing ask or now forgets what it showed, so it opens again empty; closing
        terms keeps the terms already shown."""
        with self.cond:
            if not ask:
                self.questions, self.questions_at, self.asked, self.forced_at = (), None, None, None
            if not now:
                self.now, self.now_at = "", None
            self.sections = {"ask": ask, "terms": terms, "now": now}
            self.list_only = list_only
            self.emitter.note("sections", **self.sections, list_only=list_only)
            self.cond.notify_all()

    def set_hearing(self, on: bool) -> None:
        """Whether CallCoach hears me. While it does not, the question asked of me is cleared and not asked for."""
        with self.cond:
            if on != self.hearing_me:
                self.hearing_log.append((self.clock(), on))
            self.hearing_me = on
            if not on:
                self._show_asked(None, [])
            self.cond.notify_all()

    def set_paused(self, on: bool) -> None:
        """While the recording is paused nothing reaches the live step; the times are kept for the recap."""
        with self.cond:
            if not self.pause_log or self.pause_log[-1][1] != on:
                if on or self.pause_log:
                    self.pause_log.append((self.clock(), on))

    def add_context(self, text: str) -> None:
        """A note I added. It can steer the live questions, so while they are on it asks the live step at once and
        the questions it brings replace the shown ones."""
        with self.cond:
            self.notes.append(text.strip())
            self.noted = self.coach and self.live_questions and self.sections["ask"]
            self.cond.notify_all()

    def context_notes(self) -> list[str]:
        with self.cond:
            return list(self.notes)

    def _too_soon(self, now: float) -> bool:
        """An unforced card waits card_gap_s after the last card (half of it at the active level)."""
        gap = self.card_gap_s / 2 if self.level == "active" else self.card_gap_s
        return self.last_card_at is not None and now - self.last_card_at < gap

    def set_level(self, level: str) -> None:
        with self.cond:
            self.level = level
            self.cond.notify_all()

    def shown(self, card_id: int, t: float) -> None:
        with self.cond:
            card = self.cards.get(card_id)
            if card:
                card["shown"] = True
        self.emitter.note("shown", card_id=card_id, t=t)

    def status(self) -> dict:
        with self.cond:
            return {"agent": self.state, "level": self.level, "visible": self.visible}

    def goal_list(self) -> list[Goal]:
        with self.cond:
            return list(self.goals.values())

    def set_goals(self, goals: list[Goal]) -> None:
        """Goals that came after the call started replace the ones it started with, unless I already work with
        those: a goal set by hand or a focus keeps them, so "שאל על זה" is never undone by goals that come later."""
        with self.cond:
            if self.closed:
                return
            if self.focus is not None or self.by_hand:
                self.emitter.note("goals_kept", count=len(goals))
                return
            self.goals = {g.id: g for g in goals}
            self.by_hand.clear()
            self.focus = None
            self.emitter.emit("goals", goals=[g.to_dict() for g in goals])

    def set_goal(self, goal_id: str, done: bool) -> None:
        """A goal checked or unchecked by hand. The coach no longer changes it, and a checked goal is no longer the
        focus."""
        with self.cond:
            goal = self.goals.get(goal_id)
            if goal is None or self.closed:
                self.emitter.note("unknown_goal", id=goal_id)
                return
            self.by_hand.add(goal_id)
            self.goals[goal_id] = dataclasses.replace(goal, done=done)
            if done and self.focus == goal_id:
                self.focus = None
            self.emitter.emit("goals", goals=[g.to_dict() for g in self.goals.values()])

    def focus_goal(self, goal_id: str | None) -> None:
        """"שאל על זה": the live questions aim at this open goal from now until it is reached, another goal is
        chosen, or the focus ends (None). Choosing one asks the live step at once."""
        with self.cond:
            if goal_id is not None and (goal_id not in self.goals or self.goals[goal_id].done):
                self.emitter.note("unknown_goal", id=goal_id)
                return
            self.focus = goal_id
            self.focus_asked = (goal_id is not None and self.coach and self.live_questions
                                and self.sections["ask"])
            self.cond.notify_all()

    def term_list(self) -> list[dict]:
        with self.cond:
            return [dict(t) for t in self.terms.values()]

    def open(self) -> None:
        if self.provider is None:
            return
        try:
            self.provider.open_call(self.system, REPLY_SCHEMA)
            self._opened = True
        except ProviderAuth as e:
            with self.cond:
                self._auth(str(e))
        except Exception as e:
            with self.cond:
                self._unavailable(str(e) or type(e).__name__)
        finally:
            drain_notes(self.provider, self.emitter)

    def pump(self) -> bool:
        with self.cond:
            self._tick(self.clock())
            req = self._next_request(self.clock())
        if req:
            self._send(req)
        return req is not None

    def _run(self) -> None:
        if self.coach:
            self.open()
        while True:
            try:
                req = self._wait_for_request()
                if req is None:
                    return
                self._send(req)
                self._last_error = None
            except Exception as e:
                self._failed(e)

    def _wait_for_request(self) -> Request | None:
        with self.cond:
            while not self.closed:
                now = self.clock()
                self._tick(now)
                req = self._next_request(now)
                if req:
                    return req
                self.cond.wait(self.poll_s)
        return None

    def _failed(self, e: Exception) -> None:
        """An unexpected error is written to the log once (not again while it repeats) and the loop goes on."""
        text = f"{type(e).__name__}: {e}"
        if text != self._last_error:
            self._last_error = text
            self.emitter.note("orchestrator_error", error=type(e).__name__, message=str(e)[:200])
        with self.cond:
            if not self.closed:
                self.cond.wait(self.poll_s)

    def _tick(self, now: float) -> None:
        if (not self.coach or not self.meter or self.closed or not self.visible or self.level == "quiet"
                or not self.sections["ask"] or not self.hearing_me):
            return
        text = self.meter.tip(now, self.started)
        if text:
            self._emit_card("tip", text, "he", "", [], now, now, False)

    def _next_request(self, now: float) -> Request | None:
        if self.provider is None or self.state == "auth" or not self.coach:
            self.pending, self.forced_at, self.catch_up, self.noted = [], None, False, False
            self.focus_asked = False
            return None
        forced = self.forced_at is not None
        if forced and now - self.forced_at > self.stale_s:
            self.emitter.note("forced_expired", age=round(now - self.forced_at, 2))
            self.forced_at, forced = None, False
        if self.state == "unavailable" and now < self.retry_at:
            if not forced or self.forced_bypassed:
                return None
            self.forced_bypassed = True
        if not forced and not self.visible:
            return None
        if not forced and not any(self.model_sections().values()):
            self.pending, self.catch_up = [], False
            return None
        lines = self.pending
        if forced:
            mode, trigger_ids, trigger_t = "forced_question", [l.id for l in lines], self.forced_at
        elif self.focus_asked and self.sections["ask"] and self.state != "unavailable":
            mode, trigger_ids, trigger_t = "focus", [l.id for l in lines], now
        elif self.noted and self.sections["ask"] and self.state != "unavailable":
            mode, trigger_ids, trigger_t = "note", [l.id for l in lines], now
        elif self.catch_up or self.state == "unavailable":
            self.catch_up = False
            if not lines:
                return None
            mode, trigger_ids, trigger_t = "catch_up", [l.id for l in lines], now
        else:
            channels = (ME, THEM) if self.level == "active" or self.asked else (THEM,)
            triggers = [l for l in lines if l.channel in channels and len(l.text.split()) >= self.min_words]
            if not triggers:
                return None
            mode = "goals_only" if self.level == "quiet" or self._too_soon(now) else "normal"
            trigger_ids, trigger_t = [l.id for l in triggers], max(l.line_ready for l in triggers)
        self.pending, self.forced_at, self.noted, self.focus_asked = [], None, False, False
        req = Request(mode, lines, trigger_ids, trigger_t, forced)
        req.message = self._message(req, now)
        return req

    def _row(self, line: Line) -> str:
        return f"[{_clock_text(line.t_start - self.started)}] {line.channel} ({line.lang}): {line.text}"

    def _message(self, req: Request, now: float) -> str:
        rows = [self._row(l) for l in sorted(req.lines, key=lambda l: l.t_start)]
        state = [f"mode: {req.mode}", f"card_language: {self.lang}", f"level: {self.level}",
                 f"elapsed: {_clock_text(now - self.started)}"]
        if self.length_min:
            state.append(f"planned length: {self.length_min:g} min")
        state += ["sections: " + ", ".join(f"{s} {'on' if on else 'off'}" for s, on in self.model_sections().items()),
                  "hearing_me: " + ("on" if self.hearing_me else "off")]
        notes = [f"- {n}" for n in self.notes]
        goals = [f"- [{g.id}] {'done' if g.done else 'open'}{' (by me)' if g.id in self.by_hand else ''}: {g.text}"
                 for g in self.goals.values()]
        focus = self.goals.get(self.focus) if self.focus else None
        cards = [f"- [{'shown' if c['shown'] else 'waiting'}] {c['kind']}: {c['text']}"
                 for c in list(self.cards.values())[-CARDS_IN_STATE:]]
        known = [t["term"] for t in list(self.terms.values())[-TERMS_IN_STATE:]]
        known += list(self.ignored_names[:TERMS_IN_STATE])
        questions = ["live_questions: " + ("on" if self.live_questions else "off")]
        if self.live_questions:
            questions.append("shown_questions: " + (" | ".join(q.text for q in self.questions) or "(none)"))
        return "\n".join(["## New lines", *(rows or ["(none)"]), "## State", *state,
                          *(["my_notes:", *notes] if notes else []), "goals:",
                          *(goals or ["(none)"]),
                          "focus_goal: " + (f"[{focus.id}] {focus.text}" if focus else "(none)"),
                          "shown_cards:", *(cards or ["(none)"]),
                          "known_terms: " + (", ".join(known) or "(none)"), *questions,
                          "shown_asked: " + (self.asked.text if self.asked else "(none)"),
                          *(["shown_now: " + (self.now or "(none)")] if self.sections["now"] else [])]) + "\n"

    def _send(self, req: Request) -> None:
        if not self._opened:
            self.open()
            if not self._opened:
                with self.cond:
                    self.pending = req.lines + self.pending
                return
        req.requested_at = self.clock()
        try:
            result = self._step(req.message)
        except ProviderAuth as e:
            with self.cond:
                self._auth(str(e))
            return
        except Exception as e:
            with self.cond:
                self._unavailable(str(e) or type(e).__name__)
                self.pending = req.lines + self.pending
                if req.forced and self.forced_at is None and self.sections["ask"]:
                    self.forced_at = req.trigger_t
            return
        received = self.clock()
        self.on_usage(result.model, result.usage)
        self.on_feature("live", result.usage)
        with self.cond:
            if self.state == "unavailable":
                self.state = "ok"
                self.emitter.note("agent_recovered")
            self._apply(req, result.data, received)

    def _step(self, message: str):
        try:
            return self.provider.step(message)
        finally:
            drain_notes(self.provider, self.emitter)

    def _apply(self, req: Request, data, received: float) -> None:
        """A reply: goals always; terms, the now line, questions, the question asked of me and the card only for the
        sections open now, and the last four only while the reply is fresh."""
        ask = self.sections["ask"]
        if not ask:
            allowed = ()
        elif req.forced:
            allowed = ("question",)
        elif req.mode in ("goals_only", "note", "focus") or self.level == "quiet" or self._too_soon(received):
            allowed = ()
        else:
            allowed = tuple(k for k in self.call_type.cards if k != "term")
        try:
            reply = parse_reply(data, allowed, forced=req.forced)
        except BadReply as e:
            self.emitter.note("bad_reply", error=str(e)[:200])
            return
        for problem in reply.problems:
            self.emitter.note("reply_problem", problem=problem)
        self._update_goals(reply.goals)
        if self.closed:
            return
        if self.model_sections()["terms"]:
            for term in reply.terms:
                self._emit_term(term.term, term.note, term.kind, "he", req.trigger_ids)
        age = received - req.trigger_t
        fresh = age <= self.stale_s
        if reply.now and self.sections["now"] and fresh:
            self._emit_topic(reply.now, req.trigger_ids, received)
        if ask and fresh:
            if reply.questions and self.live_questions:
                self._emit_questions(questions_for_call(reply.questions, self.lang), req.trigger_ids, received,
                                     steered=req.mode in ("note", "focus"))
            if self.hearing_me:
                self._show_asked(reply.asked, req.trigger_ids)
        if reply.card is None:
            return
        if not fresh:
            self.emitter.note("agent_stale", card_kind=reply.card.kind, age=round(age, 2))
            return
        card = card_for_call(reply.card, self.lang)
        self._emit_card(card.kind, card.text, card.lang, card.he, req.trigger_ids, req.requested_at, received,
                        req.forced)

    def _update_goals(self, updates: dict[str, bool]) -> None:
        changed = False
        for goal_id, done in updates.items():
            goal = self.goals.get(goal_id)
            if goal is None:
                self.emitter.note("unknown_goal", id=goal_id)
            elif goal_id in self.by_hand:
                continue
            elif goal.done != done:
                self.goals[goal_id] = dataclasses.replace(goal, done=done)
                changed = True
                if done and self.focus == goal_id:
                    self.focus = None
        if changed:
            self.emitter.emit("goals", goals=[g.to_dict() for g in self.goals.values()])

    def _emit_card(self, kind: str, text: str, lang: str, he: str, trigger_ids: list[int], requested_at: float,
                   received_at: float, forced: bool) -> None:
        card_id = next(self._ids)
        if not forced:
            self.last_card_at = received_at
        self.cards[card_id] = {"kind": kind, "text": text, "shown": False}
        self.emitter.emit("card", id=card_id, kind=kind, text=text, lang=lang, he=he, trigger_line_ids=trigger_ids,
                          requested_at=round(requested_at, 3), received_at=round(received_at, 3), forced=forced)

    def _emit_term(self, term: str, note: str, kind: str, lang: str, trigger_ids: list[int]) -> int | None:
        """A term shows once per call; a late one still shows, since the list stays on screen. A term that is in a
        knowledge file shows its entry, and a term of the brief its note, instead of the agent's note. The term's
        id, a shown one's too."""
        entry = self.knowledge.lookup(term)
        brief = self.brief_terms.lookup(term) if entry is None else None
        if entry is not None:
            return self._emit_entry(entry, trigger_ids, term)
        if brief is not None:
            return self._emit_brief_term(brief, trigger_ids, term)
        return self._add_term(term, note, kind, lang, trigger_ids)

    def _emit_entry(self, entry: Entry, trigger_ids: list[int], said: str = "") -> int | None:
        """A known name, as it was said when that was in English (see Entry.shown); once per entry. Its פרטים are
        its detail, so a click shows them at once with no model request."""
        return self._add_term(entry.shown(said), entry.summary, entry.kind, entry.lang, trigger_ids, ours=entry.ours,
                              key=entry.name, file=entry.file, detail="\n".join(entry.details),
                              local=entry.local)

    def _emit_brief_term(self, entry: Entry, trigger_ids: list[int], said: str = "") -> int | None:
        """A term from the brief shows like a term the agent found: not known, with the brief's Hebrew note."""
        return self._add_term(entry.shown(said), entry.summary, "term", "he", trigger_ids, key=entry.name)

    def _add_term(self, term: str, note: str, kind: str, lang: str, trigger_ids: list[int], *,
                  ours: tuple[str, ...] | None = None, key: str = "", file: str = "", detail: str = "",
                  local: bool = False) -> int | None:
        """A new term shows; the id of the term, or None for one without letters or one I marked as known. A
        `detail` from a knowledge file is kept as the term's detail and as `file_detail`, which the model's answer
        to "עוד מהמודל" is added under; `local` marks a name from a file that is never sent to a model."""
        key = fold(key or term)
        if not key or key in self.ignored or fold(term) in self.ignored:
            return None
        if key in self.terms:
            return self.terms[key]["id"]
        term_id = len(self.terms) + 1
        known = ours is not None
        self.terms[key] = {"id": term_id, "term": term, "note": note, "kind": kind, "lang": lang, "known": known,
                           "ours": list(ours or ())}
        if detail:
            self.terms[key].update(detail=detail, file_detail=detail, local=local)
        self.emitter.emit("term", id=term_id, term=term, note=note, kind=kind, lang=lang,
                          trigger_line_ids=trigger_ids, known=known, ours=[ours_line(o) for o in ours or ()],
                          ours_links=[ours_link(o) for o in ours or ()], file=file, detail=bool(detail))
        return term_id

    def explain_term(self, term_id: int, more: bool = False) -> None:
        """A click on a term: a few Hebrew sentences on it from the live model, on a thread of its own so the live
        step never waits. A term asked again answers from what it has; one being explained waits for that answer.
        A known name's detail comes from its file, so it answers at once. `more` ("עוד מהמודל") asks the model
        about such a name all the same, once: its answer is added under the file's detail. On any other term, or
        once the answer was added, `more` is a plain click."""
        with self.cond:
            if term_id in self.explaining:
                return
            term = self._term(term_id)
            more = more and bool(term and term.get("file_detail")) and term["detail"] == term["file_detail"]
            if not self.coach and (more or not (term and term.get("detail"))):
                self.emitter.emit("term_detail", id=term_id, ok=False, text=COACH_OFF)
                return
            answer = self._answer_now(term, more)
            if answer is not None:
                self.emitter.emit("term_detail", id=term_id, ok=answer[0], text=answer[1])
                return
            self.explaining.add(term_id)
            message = self._detail_message(term, self.clock(), more)
            rules = EXPLAIN_MORE_RULES if self._with_file(term, more) else EXPLAIN_RULES
        threading.Thread(target=self._explain, args=(term_id, message, rules, more), name="explain",
                         daemon=True).start()

    def terms_now(self) -> None:
        """A press on "מה המושג שנאמר עכשיו?": the live model looks at once for the terms of the last
        TERMS_NOW_WINDOW_S seconds, on a thread of its own beside the live step, which it neither waits for nor
        changes. Its terms show like the live step's, and terms_now names them all, newest first, the ones already
        shown too, so the panel can point at the one just said. One search at a time; nothing said in that time
        answers at once with no request."""
        with self.cond:
            if self.spotting:
                return
            if (self.closed or self.provider is None or self.state == "auth" or not self.coach
                    or not self.sections["terms"]):
                self.emitter.emit("terms_now", ok=False, ids=[], picked=False)
                return
            message = self._spot_message(self.clock())
            if message is None:
                self.emitter.emit("terms_now", ok=True, ids=[], picked=False)
                return
            self.spotting = True
        threading.Thread(target=self._spot, args=(message,), name="terms-now", daemon=True).start()

    def _spot_message(self, now: float) -> str | None:
        rows = [self._row(l) for l in sorted(self.history, key=lambda l: l.t_start)
                if l.speech_end >= now - TERMS_NOW_WINDOW_S]
        if self.partial is not None and now - self.partial[1] <= PARTIAL_FRESH_S:
            rows.append(f"[still speaking] {THEM}: {self.partial[0]}")
        if not rows:
            return None
        shown = [t["term"] for t in list(self.terms.values())[-TERMS_IN_STATE:]]
        return "\n".join(["## Transcript, the last 30 seconds, newest last", *rows,
                          "## Shown terms", ", ".join(shown) or "(none)"]) + "\n"

    def _spot(self, message: str, rules: str = SPOT_RULES, ids: list[int] | None = None) -> None:
        """The search for what was just said, or the check of picked words (`ids` then holds the ones a knowledge
        file or the brief already had)."""
        found = None
        try:
            result = self.provider.spot(rules, message)
            self.on_usage(result.model, result.usage)
            self.on_feature("terms_now", result.usage)
            found, problems = parse_spotted(result.data)
            for problem in problems:
                self.emitter.note("terms_now_problem", problem=problem)
        except Exception as e:
            self.emitter.note("terms_now_failed", error=type(e).__name__, message=str(e)[:200])
        finally:
            drain_notes(self.provider, self.emitter)
        with self.cond:
            picked = ids is not None
            if not picked:
                self.spotting = False
            ids = list(ids or [])
            if found is not None and self.sections["terms"] and not self.closed:
                for term in found:
                    term_id = self._emit_term(term.term, term.note, term.kind, "he", [])
                    if term_id is not None and term_id not in ids:
                        ids.append(term_id)
            self.emitter.emit("terms_now", ok=found is not None or bool(ids), ids=ids, picked=picked)

    def check_terms(self, phrases: list[str]) -> None:
        """Words I picked from what was just said, each a term I want to know. A phrase a knowledge file or the brief
        has shows at once with no request; the rest go to the live model in one request on a thread of its own,
        beside the live step, with the transcript of the last DETAIL_WINDOW_S seconds, so it can tell what the
        transcription misheard. The answer is terms_now with picked true, its ids in the order picked."""
        with self.cond:
            if not self.sections["terms"] or self.closed:
                self.emitter.emit("terms_now", ok=False, ids=[], picked=True)
                return
            ids: list[int] = []
            ask: list[str] = []
            for phrase in (" ".join(p.split()) for p in phrases):
                term_id = self._known_term(phrase)
                if term_id is None:
                    ask.append(phrase)
                elif term_id not in ids:
                    ids.append(term_id)
            if ask and not self.coach:
                self.emitter.note("coach_off")
                self.emitter.emit("terms_now", ok=bool(ids), ids=ids, picked=True)
                return
            if not ask or self.provider is None or self.state == "auth":
                self.emitter.emit("terms_now", ok=not ask, ids=ids, picked=True)
                return
            message = self._check_message(ask, self.clock())
        threading.Thread(target=self._spot, args=(message, CHECK_RULES, ids), name="check-terms", daemon=True).start()

    def _known_term(self, phrase: str) -> int | None:
        """The id of the knowledge entry or brief term a picked phrase names, shown now if it was not yet."""
        for source, emit in ((self.knowledge, self._emit_entry), (self.brief_terms, self._emit_brief_term)):
            entry = source.lookup(phrase)
            said = phrase
            if entry is None:
                named = source.said_in(phrase)
                entry, said = named[0] if named else (None, "")
            if entry is not None:
                return emit(entry, [], said)
        return None

    def _check_message(self, phrases: list[str], now: float) -> str:
        lines = sorted((l for l in self.history if l.t_start >= now - DETAIL_WINDOW_S), key=lambda l: l.t_start)
        rows = [self._row(l) for l in lines]
        if self.partial is not None and now - self.partial[1] <= PARTIAL_FRESH_S:
            rows.append(f"[still speaking] {THEM}: {self.partial[0]}")
        shown = [t["term"] for t in list(self.terms.values())[-TERMS_IN_STATE:]]
        return "\n".join(["## Picked", *[f"- {p}" for p in phrases], "## Transcript, the last 3 minutes",
                          *(rows or ["(none)"]), "## Shown terms", ", ".join(shown) or "(none)"]) + "\n"

    def add_screen(self, path: str, text: str) -> None:
        """A screenshot of the call app's window, and a note with it. The note is added at once; the live model, on a
        thread of its own beside the live step, says in one Hebrew line what the screen shows and names its terms.
        The line becomes a note too, so the live step, the recap and the summary read it, and the terms show like
        the live step's. The answer is screen."""
        if text.strip():
            self.add_context(text)
        with self.cond:
            if not self.coach:
                self.emitter.emit("screen", ok=False, text=COACH_OFF)
                return
            if self.closed or self.provider is None or self.state == "auth":
                self.emitter.emit("screen", ok=False, text=SCREEN_FAILED)
                return
            message = self._screen_message(text, self.clock())
        threading.Thread(target=self._look, args=(path, message), name="screen", daemon=True).start()

    def _screen_message(self, text: str, now: float) -> str:
        lines = sorted((l for l in self.history if l.t_start >= now - SCREEN_WINDOW_S), key=lambda l: l.t_start)
        shown = [t["term"] for t in list(self.terms.values())[-TERMS_IN_STATE:]]
        return "\n".join(["## The call", f"title: {self.title or '(none)'}", "## What I wrote with it",
                          text.strip() or "(nothing)", "## Transcript, the last 2 minutes",
                          *([self._row(l) for l in lines] or ["(none)"]), "## Shown terms",
                          ", ".join(shown) or "(none)"]) + "\n"

    def _look(self, path: str, message: str) -> None:
        line, terms = "", ()
        try:
            file = Path(path)
            media_type = MEDIA_TYPES.get(file.suffix.lower())
            image = file.read_bytes()
            if media_type is None or not image or len(image) > MAX_SCREEN_BYTES:
                raise ValueError(f"not a screenshot CallCoach can send: {file.name}, {len(image)} bytes")
            result = self.provider.look(SCREEN_RULES, message, image, media_type)
            self.on_usage(result.model, result.usage)
            self.on_feature("screen", result.usage)
            line, terms, problems = parse_screen(result.data)
            for problem in problems:
                self.emitter.note("screen_problem", problem=problem)
        except Exception as e:
            self.emitter.note("screen_failed", error=type(e).__name__, message=str(e)[:200])
        finally:
            drain_notes(self.provider, self.emitter)
        with self.cond:
            if line:
                self.notes.append(SCREEN_NOTE + line)
            if self.sections["terms"] and not self.closed:
                for term in terms:
                    self._emit_term(term.term, term.note, term.kind, "he", [])
            self.cond.notify_all()
            self.emitter.emit("screen", ok=bool(line), text=line or SCREEN_FAILED)

    def _term(self, term_id: int) -> dict | None:
        return next((t for t in self.terms.values() if t["id"] == term_id), None)

    def _answer_now(self, term: dict | None, more: bool = False) -> tuple[bool, str] | None:
        """The answer that needs no request: the detail the term has, or why there is none. A call that ended
        asks for nothing more. Asked for more, a file's detail is not the answer."""
        if term is None:
            return False, NO_SUCH_TERM
        if term.get("detail") and not more:
            return True, term["detail"]
        if self.closed:
            return False, CALL_ENDED
        if self.provider is None or self.state == "auth":
            return False, DETAIL_FAILED
        return None

    def _detail_message(self, term: dict, now: float, more: bool = False) -> str:
        """The term, what its file already says when more is asked for, and the transcript of the last 3 minutes.
        A file marked local only is never sent, so more on its name is asked like any term."""
        lines = sorted((l for l in self.history if l.t_start >= now - DETAIL_WINDOW_S), key=lambda l: l.t_start)
        known = ["## What my knowledge file already says", term["file_detail"]] if self._with_file(term, more) else []
        return "\n".join(["## Term", f"term: {term['term']}", f"kind: {term['kind']}", f"note: {term['note']}", *known,
                          "## Transcript, the last 3 minutes", *([self._row(l) for l in lines] or ["(none)"])]) + "\n"

    @staticmethod
    def _with_file(term: dict | None, more: bool) -> bool:
        """Whether a request for more carries what the file says: not for a file marked local only."""
        return more and bool(term) and not term.get("local")

    def _explain(self, term_id: int, message: str, rules: str = EXPLAIN_RULES, more: bool = False) -> None:
        text = ""
        try:
            result = self.provider.explain(rules, message)
            self.on_usage(result.model, result.usage)
            self.on_feature("explain", result.usage)
            text = parse_detail(result.data)
            if not text:
                self.emitter.note("detail_empty", id=term_id)
        except Exception as e:
            self.emitter.note("detail_failed", id=term_id, error=type(e).__name__, message=str(e)[:200])
        finally:
            drain_notes(self.provider, self.emitter)
        with self.cond:
            self.explaining.discard(term_id)
            if text and more:
                # The file's detail stays first and the model's answer goes under it, so "עוד מהמודל" adds to what
                # the file says and never replaces it.
                term = self._term(term_id)
                text = term["detail"] = f"{term['file_detail']}\n\n{text}"
            elif text:
                self._term(term_id)["detail"] = text
            self.emitter.emit("term_detail", id=term_id, ok=bool(text), text=text or DETAIL_FAILED)

    def _emit_questions(self, questions: tuple[Question, ...], trigger_ids: list[int], at: float,
                        steered: bool = False) -> None:
        """The questions to ask next replace the shown ones, but shown live questions stay QUESTIONS_HOLD_S first, so
        they can be read and asked; the same questions again change nothing. Opening questions give way at once, and
        so do all shown questions when the questions answer a note I just added."""
        if [fold(q.text) for q in questions] == [fold(q.text) for q in self.questions]:
            return
        if (not steered and self.questions and self.questions_at is not None
                and at - self.questions_at < QUESTIONS_HOLD_S):
            return
        self.questions, self.questions_at = questions, at
        self.emitter.emit("questions", items=[q.to_dict() for q in questions], lang=self.lang,
                          trigger_line_ids=trigger_ids)

    def _show_questions(self, questions: tuple[Question, ...], trigger_ids: list[int]) -> None:
        """Opening questions, shown in the direction of their own text; the first live questions replace them."""
        self.questions, self.questions_at = tuple(questions), None
        self.emitter.emit("questions", items=[q.to_dict() for q in questions],
                          lang=text_lang(q.text for q in questions), trigger_line_ids=trigger_ids)

    def offer_questions(self, questions: tuple[Question, ...]) -> None:
        """Opening questions that came after the call started (from the background request) show only while nothing
        else was: no opening questions from the start and no live questions yet, and ask is open."""
        with self.cond:
            if self.closed or not self.sections["ask"] or self.questions or not questions:
                return
            self._show_questions(questions, [])

    def _show_asked(self, asked: Question | None, trigger_ids: list[int]) -> None:
        """The question asked of me: a new or changed one is sent, and null clears the shown one (text ""); the same
        question again sends nothing."""
        shown = self.asked
        if asked is None and shown is None:
            return
        if asked is not None and shown is not None and fold(asked.text) == fold(shown.text):
            return
        self.asked = asked
        self.emitter.emit("asked", text=asked.text if asked else "", he=asked.he if asked else "",
                          trigger_line_ids=trigger_ids)

    def _emit_topic(self, text: str, trigger_ids: list[int], at: float) -> None:
        """A new line on what is discussed now; a shown line stays NOW_HOLD_S first, so the panel does not keep
        changing under the eye; the same line again sends nothing."""
        if fold(text) == fold(self.now):
            return
        if self.now and self.now_at is not None and at - self.now_at < NOW_HOLD_S:
            return
        self.now, self.now_at = text, at
        self.emitter.emit("topic", text=text, trigger_line_ids=trigger_ids)

    def recap(self, since: bool = False) -> None:
        """The recap ("עד עכשיו"): a few Hebrew lines on the call so far from the live model, on a thread of its own
        so the live step never waits. One at a time; while no line came since the last answer, that answer comes
        back at once. A call that ended asks for nothing more.

        With since, after a first answer: only what is new since the answers so far, which the app adds under them
        as "מאז <time>". While no line came since, the answer is at once an empty text."""
        with self.cond:
            if self.recapping:
                return
            more = since and bool(self.recap_said)
            answer = self._recap_now(more)
            if answer is not None:
                self.emitter.emit("recap", **answer)
                return
            self.recapping = True
            lines = len(self.history)
            now = self.clock()
            upto = max((l.speech_end for l in self.history), default=now)
            message = self._recap_since_message(now) if more else self._recap_message(now)
        threading.Thread(target=self._recap, args=(message, lines, more, upto), name="recap", daemon=True).start()

    def _since_label(self) -> str:
        return _clock_text(max(0.0, (self.recap_upto or self.started) - self.started))

    def _recap_now(self, more: bool) -> dict | None:
        since = {"since": self._since_label()} if more else {}
        if self.closed:
            return {"ok": False, "text": CALL_ENDED, **since}
        if not self.coach:
            return {"ok": False, "text": COACH_OFF, **since}
        if more and self.recap_seen == len(self.history):
            return {"ok": True, "text": "", **since}
        if not more and self.recap_answer is not None and self.recap_answer[0] == len(self.history):
            return {"ok": True, "text": self.recap_answer[1]}
        if self.provider is None or self.state == "auth":
            return {"ok": False, "text": RECAP_FAILED, **since}
        return None

    def _recap_since_message(self, now: float) -> str:
        start = max((self.recap_upto or self.started) - RECAP_SINCE_OVERLAP_S, now - RECAP_WINDOW_S)
        lines = sorted((l for l in self.history if l.speech_end > start), key=lambda l: l.t_start)
        notes = [f"- {n}" for n in filter(None, [self.call_notes, *self.notes])]
        told = [f"- {row}" for text in self.recap_said for row in text.splitlines() if row.strip()]
        return "\n".join(["## Call", f"title: {self.title or '(none)'}", "my_notes:", *(notes or ["(none)"]),
                          "## What I was already told", *told,
                          *self._unheard_rows(now), *self._paused_rows(),
                          f"## Transcript since {self._since_label()}", *([self._row(l) for l in lines] or ["(none)"])]) + "\n"

    def _recap_message(self, now: float) -> str:
        lines = sorted((l for l in self.history if l.t_start >= now - RECAP_WINDOW_S), key=lambda l: l.t_start)
        notes = [f"- {n}" for n in filter(None, [self.call_notes, *self.notes])]
        return "\n".join(["## Call", f"title: {self.title or '(none)'}", "my_notes:", *(notes or ["(none)"]),
                          *self._unheard_rows(now), *self._paused_rows(),
                          "## Transcript, the last 30 minutes", *([self._row(l) for l in lines] or ["(none)"])]) + "\n"

    def _unheard_rows(self, now: float) -> list[str]:
        """The times CallCoach did not hear me so far, so a recap never takes them for silence."""
        rows, off = [], None
        for t, on in self.hearing_log:
            if not on and off is None:
                off = t
            elif on and off is not None:
                rows.append(f"- {_clock_text(off - self.started)} to {_clock_text(t - self.started)}")
                off = None
        if off is not None:
            rows.append(f"- {_clock_text(off - self.started)} to now")
        return ["## Times CallCoach did not hear me", *rows] if rows else []

    def _paused_rows(self) -> list[str]:
        """The times the recording was paused so far: nothing from either side was heard then."""
        rows, since = [], None
        for t, on in self.pause_log:
            if on and since is None:
                since = t
            elif not on and since is not None:
                rows.append(f"- {_clock_text(since - self.started)} to {_clock_text(t - self.started)}")
                since = None
        if since is not None:
            rows.append(f"- {_clock_text(since - self.started)} to now")
        return ["## Times the recording was paused (neither side was heard)", *rows] if rows else []

    def _recap(self, message: str, lines: int, more: bool = False, upto: float | None = None) -> None:
        text = ""
        try:
            result = self.provider.recap(RECAP_SINCE_RULES if more else RECAP_RULES, message)
            self.on_usage(result.model, result.usage)
            self.on_feature("recap", result.usage)
            text = parse_recap(result.data, *((MAX_SINCE_LINES, MAX_SINCE_WORDS) if more else ()))
            if not text:
                self.emitter.note("recap_empty")
        except Exception as e:
            self.emitter.note("recap_failed", error=type(e).__name__, message=str(e)[:200])
        finally:
            drain_notes(self.provider, self.emitter)
        with self.cond:
            self.recapping = False
            since = {"since": self._since_label()} if more else {}
            nothing = more and text.strip(" .") == NOTHING_NEW
            if text:
                self.recap_seen, self.recap_upto = lines, upto
                if not more:
                    self.recap_answer, self.recap_said = (lines, text), [text]
                elif not nothing:
                    self.recap_said.append(text)
            if nothing:
                self.emitter.emit("recap", ok=True, text="", **since)
            else:
                self.emitter.emit("recap", ok=bool(text), text=text or RECAP_FAILED, **since)

    def _unavailable(self, message: str) -> None:
        self.retry_at = self.clock() + self.retry_s
        if self.state != "unavailable":
            self.state = "unavailable"
            self.emitter.error("agent_unavailable", message[:200])
        else:
            self.emitter.note("agent_still_unavailable", message=message[:200])

    def _auth(self, message: str) -> None:
        self.pending, self.forced_at = [], None
        if self.state != "auth":
            self.state = "auth"
            self.emitter.error("agent_auth", message[:200])
