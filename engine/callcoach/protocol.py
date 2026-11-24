"""JSON line events (engine to app) and commands (app to engine). Version 6."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import IO

PROTOCOL = 6

EVENTS: dict[str, tuple[str, ...]] = {
    "hello": ("protocol", "version"),
    "ready": ("folder", "replay_t0"),
    "status": ("state", "elapsed", "talk_share", "lang_me", "lang_them", "conversation_lang",
               "agent", "level", "visible", "hearing", "me_voice", "paused", "transcribing"),
    "segment": ("id", "channel", "t_start", "speech_end", "closed_at", "duration"),
    "line": ("id", "segment_id", "channel", "lang", "text", "t_start", "speech_end", "line_ready",
             "conversation_lang"),
    "partial": ("channel", "text", "t_start"),
    "card": ("id", "kind", "text", "lang", "trigger_line_ids", "requested_at", "received_at", "forced", "he"),
    "term": ("id", "term", "note", "kind", "lang", "trigger_line_ids", "known", "ours", "ours_links", "file",
             "detail"),
    "term_detail": ("id", "ok", "text"),
    "questions": ("items", "lang", "trigger_line_ids"),
    "asked": ("text", "he", "trigger_line_ids"),
    "recap": ("ok", "text"),
    "topic": ("text", "trigger_line_ids"),
    "terms_now": ("ok", "ids", "picked"),
    "screen": ("ok", "text"),
    "translation": ("id", "he"),
    "goals": ("goals",),
    "finalized": ("ok", "seconds", "reason"),
    "summary_ready": ("ok", "path", "message"),
    "usage": ("call", "today", "month"),
    "account": ("ok", "provider", "name", "via_claude_code", "message"),
    "error": ("code", "message", "ends_call"),
    "stopped": (),
}

COMMANDS: dict[str, tuple[str, ...]] = {
    "start": ("call_type", "lang", "level", "text", "brief", "length_min", "goals", "glossary", "name", "title",
              "questions"),
    "stop": ("summary",),
    "action": ("id",),
    "set_visibility": ("visible",),
    "set_level": ("level",),
    "set_language": ("lang",),
    "shown": ("card_id", "t"),
    "account_check": (),
    "add_context": ("text",),
    "explain_term": ("id",),
    "set_hearing": ("on",),
    "set_paused": ("on",),
    "set_translate": ("on",),
    "set_sections": ("ask", "terms"),
    "set_coach": ("on",),
    "set_goal": ("id", "done"),
    "focus_goal": ("id",),
    "recap": (),
    "check_terms": ("terms",),
    "add_screen": ("path", "text"),
}

LANGS = ("he", "en")
LANG_CHOICES = (*LANGS, "auto")
MAX_CONTEXT = 4000
LEVELS = ("quiet", "normal", "active")
MAX_PICKED = 5
MAX_PICKED_CHARS = 60
SCREEN_SUFFIXES = (".png", ".jpg", ".jpeg")
ACTIONS = ("ask_now", "mark", "terms_now")
STATES = ("warming", "idle", "active", "stopping")
AGENT_STATES = ("off", "ok", "unavailable", "auth")
CARD_KINDS = ("question", "tip", "steer")
SECTIONS = ("ask", "terms")
# The line on what is being discussed now is a third section, optional in `set_sections` and `start.sections` so an
# app that does not know it never pays for it: a sections object without `now` keeps it off.
ALL_SECTIONS = (*SECTIONS, "now")
# "רק מהרשימה": terms only from the knowledge files and the brief, never from the live model. Optional like now, and
# false when missing.
LIST_ONLY = "list_only"

ERRORS: dict[str, bool] = {
    "bad_command": False,
    "command_failed": False,
    "capture": False,
    "source": False,
    "no_sources": True,
    "engine": True,
    "auto_stop": True,
    "recording_stopped": False,
    "transcript_stopped": False,
    "transcription_start_failed": False,
    "transcription_failed": False,
    "transcription_stopped": False,
    "langid_failed": False,
    "langid_stopped": False,
    "system_audio_dropout": False,
    "agent_unavailable": False,
    "agent_auth": False,
    "summary_failed": False,
    "export_failed": False,
    "no_agent_account": False,
}


class ProtocolError(ValueError):
    pass


def make_event(type_: str, **fields) -> dict:
    if type_ not in EVENTS:
        raise ProtocolError(f"unknown event {type_!r}")
    missing = [f for f in EVENTS[type_] if f not in fields]
    if missing:
        raise ProtocolError(f"event {type_!r} is missing {missing}")
    if type_ == "error" and fields["code"] not in ERRORS:
        raise ProtocolError(f"unknown error code {fields['code']!r}")
    return {"type": type_, "t": time.time(), **fields}


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ProtocolError(message)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_start(msg: dict) -> None:
    _require(isinstance(msg["call_type"], str) and bool(msg["call_type"]), "call_type must be a name")
    _require(msg["lang"] in LANG_CHOICES, f"unsupported language {msg['lang']!r}")
    _require(msg["level"] in LEVELS, f"unsupported level {msg['level']!r}")
    _require(isinstance(msg["text"], str), "text must be a string")
    _require(isinstance(msg["brief"], str), "brief must be a file name or empty")
    length = msg["length_min"]
    _require(length is None or (_number(length) and length > 0), "length_min must be null or positive")
    goals = msg["goals"]
    _require(isinstance(goals, list) and all(
        isinstance(g, dict) and isinstance(g.get("id"), str) and isinstance(g.get("text"), str) for g in goals),
        "goals must be a list of {id, text}")
    _require(_strings(msg["glossary"]), "glossary must be a list of strings")
    _require(isinstance(msg["name"], str), "name must be a string")
    _require(isinstance(msg["title"], str), "title must be a string")
    _require(_strings(msg["questions"]), "questions must be a list of strings")
    _require(isinstance(msg.get("replay", ""), str), "replay must be a file path")
    _require(isinstance(msg.get("record_only", False), bool), "record_only must be true or false")
    if "sections" in msg:
        sections = msg["sections"]
        _require(isinstance(sections, dict) and all(isinstance(sections.get(s), bool) for s in SECTIONS)
                 and all(isinstance(sections.get(s, False), bool) for s in ("now", LIST_ONLY)),
                 "sections must have ask and terms, each true or false, and now and list_only when they are there")


CHECKS = {
    "start": _check_start,
    "action": lambda m: _require(m["id"] in ACTIONS, f"unsupported action {m['id']!r}"),
    "set_visibility": lambda m: _require(isinstance(m["visible"], bool), "visible must be true or false"),
    "set_level": lambda m: _require(m["level"] in LEVELS, f"unsupported level {m['level']!r}"),
    "set_language": lambda m: _require(m["lang"] in LANG_CHOICES, f"unsupported language {m['lang']!r}"),
    "shown": lambda m: _require(_integer(m["card_id"]) and _number(m["t"]),
                                "shown needs an integer card_id and a time"),
    "add_context": lambda m: _require(isinstance(m["text"], str) and 0 < len(m["text"].strip()) <= MAX_CONTEXT,
                                      f"add_context needs text of 1 to {MAX_CONTEXT} characters"),
    "explain_term": lambda m: _require(_integer(m["id"]) and isinstance(m.get("more", False), bool),
                                       "explain_term needs an integer term id and more true or false"),
    "set_hearing": lambda m: _require(isinstance(m["on"], bool), "on must be true or false"),
    "set_paused": lambda m: _require(isinstance(m["on"], bool), "on must be true or false"),
    "set_translate": lambda m: _require(isinstance(m["on"], bool), "on must be true or false"),
    "recap": lambda m: _require(isinstance(m.get("since", False), bool), "since must be true or false"),
    "set_sections": lambda m: _require(all(isinstance(m[s], bool) for s in SECTIONS)
                                       and all(isinstance(m.get(s, False), bool) for s in ("now", LIST_ONLY)),
                                       "ask, terms, now and list_only must be true or false"),
    "stop": lambda m: _require(isinstance(m["summary"], bool), "summary must be true or false"),
    "set_coach": lambda m: _require(isinstance(m["on"], bool), "on must be true or false"),
    "set_goal": lambda m: _require(isinstance(m["id"], str) and bool(m["id"]) and isinstance(m["done"], bool),
                                   "set_goal needs a goal id and done true or false"),
    "focus_goal": lambda m: _require(m["id"] is None or (isinstance(m["id"], str) and bool(m["id"])),
                                     "focus_goal needs a goal id or null"),
    "check_terms": lambda m: _require(
        isinstance(m["terms"], list) and 0 < len(m["terms"]) <= MAX_PICKED and all(
            isinstance(t, str) and 0 < len(t.strip()) <= MAX_PICKED_CHARS for t in m["terms"]),
        f"check_terms needs 1 to {MAX_PICKED} terms of 1 to {MAX_PICKED_CHARS} characters"),
    "add_screen": lambda m: _require(
        isinstance(m["path"], str) and m["path"].lower().endswith(SCREEN_SUFFIXES) and isinstance(m["text"], str)
        and len(m["text"]) <= MAX_CONTEXT, f"add_screen needs a .png or .jpg path and text of at most {MAX_CONTEXT}"),
}


def parse_command(raw: str) -> dict:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"command is not JSON: {e}") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str) or msg["type"] not in COMMANDS:
        raise ProtocolError(f"unknown command {raw.strip()[:80]!r}")
    missing = [f for f in COMMANDS[msg["type"]] if f not in msg]
    if missing:
        raise ProtocolError(f"command {msg['type']!r} is missing {missing}")
    check = CHECKS.get(msg["type"])
    if check:
        check(msg)
    return msg


class Emitter:
    """Writes events to the app (stdout) and to events.log. After `stopped`, events go to the log only."""

    def __init__(self, out: IO[str] | None = None, log: Path | None = None):
        self.out = sys.stdout if out is None else out
        self.log = log
        self.stopped = False
        self._lock = threading.Lock()

    def emit(self, type_: str, **fields) -> dict:
        event = make_event(type_, **fields)
        self._write(json.dumps(event, ensure_ascii=False), to_app=True)
        if type_ == "stopped":
            self.stopped = True
        return event

    def error(self, code: str, message: str) -> dict:
        return self.emit("error", code=code, message=message, ends_call=ERRORS[code])

    def note(self, kind: str, **fields) -> None:
        self._write(json.dumps({"type": "note", "t": time.time(), "kind": kind, **fields}, ensure_ascii=False),
                    to_app=False)

    def set_log(self, log: Path | None) -> None:
        with self._lock:
            self.log = log

    def _write(self, text: str, to_app: bool) -> None:
        with self._lock:
            if to_app and self.out and not self.stopped:
                try:
                    self.out.write(text + "\n")
                    self.out.flush()
                except (OSError, ValueError):
                    self.out = None
            if self.log:
                try:
                    with open(self.log, "a", encoding="utf-8") as f:
                        f.write(text + "\n")
                except OSError:
                    self.log = None
