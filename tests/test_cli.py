import json
import os
import signal

import pytest

from callcoach.__main__ import build_parser, call_folder, cmd_recover, cmd_run


def test_call_folders_are_unique(tmp_path):
    a = call_folder(tmp_path, "Demo call!")
    b = call_folder(tmp_path, "Demo call!")
    assert a != b and a.parent == tmp_path / "calls"
    assert a.name.endswith("_Demo-call")


def test_run_arguments():
    args = build_parser().parse_args(["run", "--replay", "x.wav", "--lang", "en"])
    assert (args.replay, args.lang, args.fast, args.app) == ("x.wav", "en", False, False)


def test_watch_is_gone():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--replay", "x.wav", "--watch"])


def test_ctrl_c_while_the_servers_load_stops_them(tmp_path, monkeypatch):
    import callcoach.transcriber as tr

    stopped = []

    class Loading:
        def __init__(self, servers, on_error=None):
            pass

        def start(self):
            raise KeyboardInterrupt

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(tr, "ServerTranscriber", Loading)
    monkeypatch.setattr(tr, "default_servers", lambda log_dir, vad=True, on_note=None: {})
    old = signal.getsignal(signal.SIGTERM)
    try:
        assert cmd_run(build_parser().parse_args(["run", "--data", str(tmp_path)])) == 130
    finally:
        signal.signal(signal.SIGTERM, old)
    assert stopped == [True]


def test_a_missing_whisper_server_still_ends_with_stopped(tmp_path, monkeypatch, capsys):
    import callcoach.transcriber as tr

    def missing(log_dir, vad=True, on_note=None):
        raise FileNotFoundError("whisper-server not found; run ./setup.sh")

    monkeypatch.setattr(tr, "default_servers", missing)
    old = signal.getsignal(signal.SIGTERM)
    try:
        code = cmd_run(build_parser().parse_args(["run", "--data", str(tmp_path)]))
    finally:
        signal.signal(signal.SIGTERM, old)
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert code == 1
    assert [e["code"] for e in events if e["type"] == "error"] == ["transcription_start_failed"]
    assert events[-1]["type"] == "stopped"


def test_the_mic_can_be_chosen():
    assert build_parser().parse_args(["run"]).mic == "auto"
    assert build_parser().parse_args(["run", "--mic", "ffmpeg"]).mic == "ffmpeg"


def test_a_glossary_can_be_given():
    args = build_parser().parse_args(["run", "--glossary", "SOC 2, Okta"])
    assert args.glossary == "SOC 2, Okta"


def test_recover_reports_folders_it_skips(tmp_path, capsys):
    folder = tmp_path / "calls" / "2026-11-15_1000_live"
    folder.mkdir(parents=True)
    (folder / "me.wav").write_bytes(b"")
    (folder / ".recording").write_text(str(os.getpid()))
    cmd_recover(build_parser().parse_args(["recover", "--data", str(tmp_path)]))
    row = json.loads(capsys.readouterr().out.strip())
    assert row == {"folder": str(folder), "skipped": "recording in progress"}


def test_account_commands_parse():
    args = build_parser().parse_args(["account", "login", "--name", "work", "--sso"])
    assert (args.command, args.action, args.name, args.mode) == ("account", "login", "work", "sso")
    args = build_parser().parse_args(["account", "status", "--name", "work"])
    assert (args.action, args.name) == ("status", "work")
    args = build_parser().parse_args(["account", "logout", "--name", "work"])
    assert (args.action, args.name) == ("logout", "work")


def test_serve_parses():
    args = build_parser().parse_args(["serve", "--data", "/tmp/x", "--mic", "ffmpeg"])
    assert (args.command, args.data, args.mic) == ("serve", "/tmp/x", "ffmpeg")


def test_brief_parses():
    args = build_parser().parse_args(["brief", "--data", "/tmp/x"])
    assert (args.command, args.data) == ("brief", "/tmp/x")


def recording_folder(tmp_path, recording=False):
    from callcoach.recorder import LOCK_NAME

    data = tmp_path / "data"
    folder = data / "calls" / "2026-11-19-1000-lecture"
    folder.mkdir(parents=True)
    meta = {"started": 1000.0, "title": "הרצאה", "record_only": True, "call_type": "general",
            "goals": [{"id": "g1", "text": "להבין את ההרשאות", "done": True}]}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    rows = [{"kind": "line", "id": 1, "segment_id": 1, "channel": "them", "lang": "he", "text": "שלום לכולם",
             "t_start": 1005.0, "speech_end": 1007.0, "line_ready": 1008.0}]
    (folder / "lines.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")
    (folder / "terms.json").write_text(json.dumps([{"term": "OAuth", "note": "הרשאות", "kind": "term",
                                                    "lang": "en", "known": False}]), encoding="utf-8")
    if recording:
        (folder / LOCK_NAME).write_text(str(os.getpid()))
    return data, folder


def last_reply(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_summary_writes_the_summary_of_a_recording(tmp_path, monkeypatch, capsys):
    from callcoach.__main__ import main

    data, folder = recording_folder(tmp_path)
    monkeypatch.setenv("CALLCOACH_PROVIDER", "fake")
    assert main(["summary", "--data", str(data), "--call", folder.name]) == 0
    reply = last_reply(capsys)
    assert reply["ok"] and reply["path"] == str(folder / "summary.md")
    text = (folder / "summary.md").read_text(encoding="utf-8")
    assert "## מושגים שעלו" in text and "OAuth" in text and "## שימוש" not in text
    assert not (folder / "usage.json").exists()
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["summary_kind"] in ("call", "lecture")


def test_summary_without_an_account_says_so(tmp_path, monkeypatch, capsys):
    from callcoach.__main__ import main

    data, folder = recording_folder(tmp_path)
    monkeypatch.setenv("CALLCOACH_PROVIDER", "none")
    assert main(["summary", "--data", str(data), "--call", folder.name]) == 1
    reply = last_reply(capsys)
    assert not reply["ok"] and "אין חשבון פעיל" in reply["message"] and not (folder / "summary.md").exists()


def test_summary_of_a_call_still_recording_is_refused(tmp_path, monkeypatch, capsys):
    from callcoach.__main__ import main

    data, folder = recording_folder(tmp_path, recording=True)
    monkeypatch.setenv("CALLCOACH_PROVIDER", "fake")
    assert main(["summary", "--data", str(data), "--call", folder.name]) == 1
    assert "עדיין" in last_reply(capsys)["message"] and not (folder / "summary.md").exists()


def test_the_goals_and_title_of_meta_reach_the_summary_request(tmp_path):
    from callcoach.providers import FakeProvider
    from callcoach.summary import summarize_folder

    data, folder = recording_folder(tmp_path)
    fake = FakeProvider()
    result = summarize_folder(data, folder, fake)
    [(kind, message)] = fake.calls
    assert result.ok and kind == "summarize"
    assert "- answered: להבין את ההרשאות" in message and "type: general" in message


def test_the_notes_of_meta_reach_the_summary_request(tmp_path):
    from callcoach.providers import FakeProvider
    from callcoach.summary import summarize_folder

    data, folder = recording_folder(tmp_path)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    (folder / "meta.json").write_text(json.dumps({**meta, "notes": "דנה מ-Acme, בקשות גישה"}), encoding="utf-8")
    fake = FakeProvider()
    summarize_folder(data, folder, fake)
    assert "דנה מ-Acme, בקשות גישה" in fake.calls[0][1]


def test_a_summary_written_locally_is_marked_and_a_later_full_one_clears_the_mark(tmp_path, monkeypatch, capsys):
    from callcoach import summary as summary_module
    from callcoach.providers import FakeProvider, ProviderUnavailable

    class Down(FakeProvider):
        def summarize(self, system, message):
            raise ProviderUnavailable("Control request timeout: initialize")

    data, folder = recording_folder(tmp_path)
    meta = lambda: json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert not summary_module.summarize_folder(data, folder, Down()).ok and meta()["summary_local"] is True
    assert summary_module.summarize_folder(data, folder, FakeProvider()).ok and meta()["summary_local"] is False


def test_a_later_summary_is_copied_to_the_export_folder(tmp_path, monkeypatch, capsys):
    from callcoach.__main__ import main

    data, folder = recording_folder(tmp_path)
    root = tmp_path / "drive"
    root.mkdir()
    monkeypatch.setenv("CALLCOACH_PROVIDER", "fake")
    monkeypatch.setenv("CALLCOACH_EXPORT", str(root))
    assert main(["summary", "--data", str(data), "--call", folder.name]) == 0
    assert last_reply(capsys)["ok"]
    assert (root / folder.name / "summary.md").read_text(encoding="utf-8") == \
        (folder / "summary.md").read_text(encoding="utf-8")
    assert "שלום לכולם" in (root / folder.name / "transcript.md").read_text(encoding="utf-8")
