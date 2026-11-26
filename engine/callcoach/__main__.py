"""callcoach run | serve | brief | followup | summary | final | recover | account"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from callcoach import __version__
from callcoach.config import DATA_DIR, GLOSSARY_PROMPT, RATE, RUN_DIR
from callcoach.protocol import PROTOCOL, Emitter, ProtocolError, parse_command
from callcoach.store import call_folder

RESEARCH_FAILED = "(the research failed: write from the request alone)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="callcoach")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="transcribe a live call, or replay a recorded one")
    run.add_argument("--replay", help="16 kHz WAV to play instead of live capture (stereo: left = me)")
    run.add_argument("--fast", action="store_true", help="replay as fast as possible (no latency numbers)")
    run.add_argument("--lang", choices=("he", "en"), default="he", help="language the call starts in")
    run.add_argument("--name", default="call")
    run.add_argument("--data", default=str(DATA_DIR))
    run.add_argument("--vad-mode", type=int, choices=(0, 1, 2, 3), default=2)
    run.add_argument("--no-whisper-vad", action="store_true")
    run.add_argument("--app", action="store_true", help="launched by the app: stdin EOF ends the call")
    run.add_argument("--mic", choices=("auto", "helper", "ffmpeg"), default="auto",
                     help="auto: the mic helper when installed, else ffmpeg")
    run.add_argument("--glossary", default="", help="names and terms, comma separated")
    srv = sub.add_parser("serve", help="the app's engine: warm up, one call, then the summary")
    srv.add_argument("--data", default=str(DATA_DIR))
    srv.add_argument("--mic", choices=("auto", "helper", "ffmpeg"), default="auto")
    srv.add_argument("--no-whisper-vad", action="store_true")
    brief = sub.add_parser("brief", help="write a brief from one JSON request on stdin, with the web")
    brief.add_argument("--data", default=str(DATA_DIR))
    follow = sub.add_parser("followup", help="draft the follow-up email of one call into its followup.md")
    follow.add_argument("--data", default=str(DATA_DIR))
    follow.add_argument("--call", required=True, help="the call's folder, or its name in <data>/calls")
    summ = sub.add_parser("summary", help="write the summary of one finished call into its summary.md")
    summ.add_argument("--data", default=str(DATA_DIR))
    summ.add_argument("--call", required=True, help="the call's folder, or its name in <data>/calls")
    fin = sub.add_parser("final", help="transcribe a finished call again in full, as its final lines")
    fin.add_argument("--data", default=str(DATA_DIR))
    fin.add_argument("--call", required=True, help="the call's folder, or its name in <data>/calls")
    rec = sub.add_parser("recover", help="finish calls left by a crash (never while a call is running)")
    rec.add_argument("--data", default=str(DATA_DIR))
    acc = sub.add_parser("account", help="log in a subscription account folder, check it, or log it out")
    acc.add_argument("action", choices=("login", "status", "logout"))
    acc.add_argument("--name", required=True)
    mode = acc.add_mutually_exclusive_group()
    mode.add_argument("--console", dest="mode", action="store_const", const="console")
    mode.add_argument("--sso", dest="mode", action="store_const", const="sso")
    acc.set_defaults(mode="subscription")
    return p


def read_commands(engine, emitter: Emitter, stop_on_eof: bool) -> None:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            cmd = parse_command(raw)
        except ProtocolError as e:
            emitter.error("bad_command", str(e))
            continue
        if cmd["type"] == "stop":
            engine.request_stop()
        elif cmd["type"] == "set_language" and cmd["lang"] == "auto":
            engine.unlock_language()
        elif cmd["type"] == "set_language":
            engine.lock_language(cmd["lang"])
        elif cmd["type"] == "action" and cmd["id"] == "mark":
            engine.mark()
        elif cmd["type"] == "set_hearing":
            engine.set_hearing(cmd["on"])
        elif cmd["type"] == "set_paused":
            engine.set_paused(cmd["on"])
    if stop_on_eof:
        engine.request_stop()


def cmd_run(args) -> int:
    import webrtcvad

    from callcoach.aec import cancel_echo
    from callcoach.engine import CallEngine
    from callcoach.sources import FileSource, MicSource, SystemSource, echo_cancel_wanted
    from callcoach.transcriber import ServerTranscriber, default_servers
    from callcoach.whisper import reap_orphans

    folder = call_folder(Path(args.data), args.name)
    emitter = Emitter(sys.stdout, log=folder / "events.log")
    emitter.emit("hello", protocol=PROTOCOL, version=__version__)
    def on_error(message: str) -> None:
        emitter.error("capture", message)

    def on_langid_error(message: str) -> None:
        emitter.error("langid_stopped", message)

    signal.signal(signal.SIGTERM, signal.default_int_handler)
    transcriber = None
    try:
        try:
            reaped = reap_orphans(RUN_DIR)
        except OSError as e:
            emitter.note("reap_failed", message=str(e))
        else:
            if reaped:
                emitter.note("reaped", pids=reaped)
        try:
            transcriber = ServerTranscriber(default_servers(folder / "logs", vad=not args.no_whisper_vad,
                                                            on_note=emitter.note),
                                            on_error=on_langid_error)
            transcriber.start()
        except KeyboardInterrupt:
            return 130
        except Exception as e:
            emitter.error("transcription_start_failed", str(e))
            return 1
        glossary = tuple(t.strip() for t in args.glossary.split(",") if t.strip())
        if GLOSSARY_PROMPT:
            transcriber.set_glossary(glossary)
        mic = None if args.replay else MicSource(
            on_error=on_error, on_info=lambda kind: emitter.note("mic", source=kind), mode=args.mic,
            log_dir=folder / "logs", echo_cancel=echo_cancel_wanted())
        if args.replay:
            sources = [FileSource(Path(args.replay), realtime=not args.fast)]
        else:
            system = SystemSource(on_error=on_error, on_dropout=lambda s: emitter.error(
                "system_audio_dropout", f"the other side's audio dropped for {s:.1f} s (output device change?)"))
            sources = [mic, system] if mic.echo_cancel else cancel_echo([mic, system], emitter.note)

        def new_vad():
            vad = webrtcvad.Vad(args.vad_mode)
            return lambda frame: vad.is_speech(frame, RATE)

        engine = CallEngine(folder=folder, sources=sources, transcriber=transcriber, emitter=emitter,
                            new_vad=new_vad, start_lang=args.lang,
                            echo_check=lambda: mic is None or not mic.cancels_echo, glossary=glossary)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: engine.request_stop())
        threading.Thread(target=read_commands, args=(engine, emitter, args.app), daemon=True).start()
        result = engine.run()
        return 0 if result.ok else 2
    finally:
        if transcriber:
            transcriber.stop()
        emitter.emit("stopped")


def cmd_serve(args) -> int:
    from callcoach.engine import CallEngine
    from callcoach.final import spawn as spawn_final
    from callcoach.provider_factory import Account, build_provider, read_account
    from callcoach.providers import ProviderAuth
    from callcoach.session import Session, live_sources, live_transcriber, vad_factory
    from callcoach.whisper import reap_orphans

    emitter = Emitter(sys.stdout)
    try:
        reap_orphans(RUN_DIR)
    except OSError as e:
        print(f"callcoach: old whisper servers were not checked ({e})", file=sys.stderr)
    account, provider, error = Account("none", "none"), None, ""
    try:
        account, key = read_account()
        provider = build_provider(account, key)
    except (ValueError, ProviderAuth) as e:
        error = str(e)

    def on_call_start(engine) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: engine.request_stop())

    signal.signal(signal.SIGTERM, signal.default_int_handler)
    session = Session(
        data=Path(args.data), emitter=emitter, account=account, provider=provider, account_error=error,
        make_transcriber=live_transcriber(emitter, vad=not args.no_whisper_vad),
        make_sources=lambda prep, em, log_dir: live_sources(prep, em, log_dir, mic_mode=args.mic),
        make_engine=lambda **kw: CallEngine(new_vad=vad_factory(), **kw), on_call_start=on_call_start,
        after_call=lambda folder: spawn_final(folder, Path(args.data)))
    return session.run()


def research(provider, request: dict) -> str:
    """The web research for a brief, from the request alone. A failure is written to stderr (the app's log) and
    the brief is written without it; a rejected account ends the brief."""
    from callcoach.context import research_message
    from callcoach.prompts import RESEARCH_RULES
    from callcoach.providers import ProviderAuth

    try:
        return provider.research(RESEARCH_RULES, research_message(request)).data or ""
    except ProviderAuth:
        raise
    except Exception as e:
        note = {"note": "research_failed", "error": type(e).__name__, "message": str(e)[:200]}
        print(json.dumps(note, ensure_ascii=False), file=sys.stderr)
        return RESEARCH_FAILED


def cmd_brief(args) -> int:
    """Reads one request on stdin and prints one JSON line: {"ok": true, "path"} or {"ok": false, "message"}.
    Two requests: the research, with the web and the request alone, then the brief, with no tools."""
    from callcoach.brief import BriefError, read_request, save_reply
    from callcoach.context import brief_message, call_type_names, ensure_data
    from callcoach.prompts import BRIEF_RULES
    from callcoach.provider_factory import build_provider, read_account
    from callcoach.providers import ProviderAuth, ProviderIncomplete

    data, provider = Path(args.data), None
    try:
        request = read_request(sys.stdin.read())
        account, key = read_account()
        provider = build_provider(account, key)
        if provider is None:
            raise BriefError("אין חשבון פעיל: צריך לבחור חשבון בהגדרות")
        ensure_data(data)
        notes = research(provider, request)
        result = provider.brief(BRIEF_RULES, brief_message(data, request, notes, datetime.now()))
        reply = {"ok": True, "path": str(save_reply(data / "briefs", result.data, request, call_type_names(data)))}
    except BriefError as e:
        reply = {"ok": False, "message": str(e)}
    except ProviderAuth as e:
        reply = {"ok": False, "message": f"החשבון נדחה: {e}"}
    except ProviderIncomplete as e:
        reply = {"ok": False, "message": f"המודל לא סיים לכתוב את התדריך ({e}), אפשר לנסות שוב"}
    except Exception as e:
        reply = {"ok": False, "message": f"כתיבת התדריך נכשלה: {str(e) or type(e).__name__}"}
    finally:
        if provider is not None:
            provider.close()
    print(json.dumps(reply, ensure_ascii=False))
    return 0 if reply["ok"] else 1


def cmd_followup(args) -> int:
    """Drafts the follow-up email of one call into its followup.md, replacing one that is there, and prints one JSON
    line: {"ok": true, "path"} or {"ok": false, "message"}. One request to the strong model with no tools, as the
    summary makes. Nothing is sent."""
    from callcoach.followup import FollowupError, call_dir, followup_message, followup_text, write_followup
    from callcoach.prompts import FOLLOWUP_RULES
    from callcoach.provider_factory import build_provider, read_account
    from callcoach.providers import ProviderAuth, ProviderIncomplete

    data, provider = Path(args.data), None
    try:
        folder = call_dir(data, args.call)
        message = followup_message(data, folder)
        account, key = read_account()
        provider = build_provider(account, key)
        if provider is None:
            raise FollowupError("אין חשבון פעיל: צריך לבחור חשבון בהגדרות")
        result = provider.followup(FOLLOWUP_RULES, message)
        reply = {"ok": True, "path": str(write_followup(folder, followup_text(result.data)))}
    except FollowupError as e:
        reply = {"ok": False, "message": str(e)}
    except ProviderAuth as e:
        reply = {"ok": False, "message": f"החשבון נדחה: {e}"}
    except ProviderIncomplete as e:
        reply = {"ok": False, "message": f"המודל לא סיים לכתוב את הטיוטה ({e}), אפשר לנסות שוב"}
    except Exception as e:
        reply = {"ok": False, "message": f"כתיבת הטיוטה נכשלה: {str(e) or type(e).__name__}"}
    finally:
        if provider is not None:
            provider.close()
    print(json.dumps(reply, ensure_ascii=False))
    return 0 if reply["ok"] else 1


def cmd_recover(args) -> int:
    from callcoach.recorder import needs_recovery, recording_in_progress, recover_folder
    from callcoach.store import render

    calls = Path(args.data) / "calls"
    for folder in sorted(p for p in calls.iterdir() if p.is_dir()) if calls.exists() else []:
        if recording_in_progress(folder):
            if any((folder / n).exists() for n in ("me.wav", "them.wav")):
                print(json.dumps({"folder": str(folder), "skipped": "recording in progress"}, ensure_ascii=False))
            continue
        if not needs_recovery(folder):
            continue
        result = recover_folder(folder)
        if (folder / "lines.jsonl").exists():
            render(folder)
        print(json.dumps({"folder": str(folder), "ok": result.ok, "seconds": result.seconds,
                          "reason": result.reason}, ensure_ascii=False))
    return 0


def cmd_final(args) -> int:
    """The final pass of one finished call; prints one JSON line: {"ok", "lines"} or {"ok": false, "reason"}."""
    from callcoach.final import finish_call
    from callcoach.followup import FollowupError, call_dir
    from callcoach.recorder import recording_in_progress

    data = Path(args.data)
    try:
        folder = call_dir(data, args.call)
    except FollowupError as e:
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    if recording_in_progress(folder):
        print(json.dumps({"ok": False, "reason": "the call is still recording"}))
        return 1
    result = finish_call(folder, data)
    print(json.dumps({"ok": result.ok, "lines": result.lines, "reason": result.reason}, ensure_ascii=False))
    return 0 if result.ok else 1


def cmd_account(args) -> int:
    from callcoach.provider_factory import account_logout, account_status, bundled_cli, login_command

    cli = bundled_cli()
    if args.action == "status":
        print(json.dumps(account_status(args.name, cli=cli), ensure_ascii=False))
        return 0
    if args.action == "logout":
        reply = account_logout(args.name, cli=cli)
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if reply["logged_out"] else 1
    argv, env, folder = login_command(args.name, args.mode, cli=cli)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.execve(argv[0], argv, env)


def cmd_summary(args) -> int:
    """Writes the summary of one finished call into its summary.md, replacing one that is there, and prints one JSON
    line: {"ok": true, "path", "message"} or {"ok": false, "message"} (with a "path" too when the model failed and a
    local summary was written instead). A call that is still recording is left alone."""
    from callcoach.followup import FollowupError, call_dir
    from callcoach.provider_factory import build_provider, read_account
    from callcoach.providers import ProviderAuth
    from callcoach.recorder import recording_in_progress
    from callcoach.export import export_and_mark, export_root
    from callcoach.summary import summarize_folder

    data, provider = Path(args.data), None
    try:
        folder = call_dir(data, args.call)
        if recording_in_progress(folder):
            raise FollowupError("השיחה עדיין מוקלטת, אפשר לסכם אחרי שתיגמר")
        account, key = read_account()
        provider = build_provider(account, key)
        if provider is None:
            raise FollowupError("אין חשבון פעיל: צריך לבחור חשבון בהגדרות")
        result = summarize_folder(data, folder, provider)
        reply = {"ok": result.ok, "path": str(result.path), "message": result.message}
        # A copy that fails is kept in meta.json, where the call page shows it; the summary itself is written.
        export_and_mark(folder, export_root())
    except FollowupError as e:
        reply = {"ok": False, "message": str(e)}
    except ProviderAuth as e:
        reply = {"ok": False, "message": f"החשבון נדחה: {e}"}
    except Exception as e:
        reply = {"ok": False, "message": f"כתיבת הסיכום נכשלה: {str(e) or type(e).__name__}"}
    finally:
        if provider is not None:
            provider.close()
    print(json.dumps(reply, ensure_ascii=False))
    return 0 if reply["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    commands = {"run": cmd_run, "serve": cmd_serve, "brief": cmd_brief, "followup": cmd_followup,
                "summary": cmd_summary, "final": cmd_final, "recover": cmd_recover, "account": cmd_account}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
