import json

import pytest

from callcoach.__main__ import build_parser, cmd_followup
from callcoach.followup import (FollowupError, call_dir, call_language, followup_message, followup_text,
                                write_followup)
from callcoach.prompts import FOLLOWUP_RULES
from callcoach.providers import FakeProvider, ProviderAuth, ProviderIncomplete

STARTED = 1000.0


def row(i, channel, lang, text, t):
    return {"kind": "line", "id": i, "segment_id": i, "channel": channel, "lang": lang, "text": text,
            "t_start": STARTED + t, "speech_end": STARTED + t + 2, "line_ready": STARTED + t + 3}


ROWS = [row(1, "me", "en", "Thanks for joining today", 5),
        row(2, "them", "en", "We send access requests over Slack and approve them by hand", 20),
        row(3, "me", "en", "I will send you an invite for Tuesday", 300)]


def make_call(tmp_path, rows=ROWS, meta=None, summary="## מה למדתי\n- בקשות גישה בסלאק\n\n## שימוש\nבקשות: 3\n"):
    data = tmp_path / "data"
    folder = data / "calls" / "2026-11-17-1000-acme"
    folder.mkdir(parents=True)
    (data / "briefs").mkdir()
    (data / "briefs" / "acme.md").write_text("---\ntitle: Acme\n---\n## רקע\nAcme uses Okta.\n", encoding="utf-8")
    (data / "me.md").write_text("Name: Alex\nAddress me as: masculine\n", encoding="utf-8")
    meta = {"started": STARTED, "title": "שיחה עם Acme", "brief": "acme.md", **(meta or {})}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (folder / "lines.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")
    if summary is not None:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    return data, folder


def test_the_input_holds_the_call_me_the_brief_the_summary_and_the_transcript(tmp_path):
    data, folder = make_call(tmp_path)
    text = followup_message(data, folder)
    for part in ("title: שיחה עם Acme", "call language: en", "# Me\nName: Alex", "# Brief: acme.md\n## רקע\nAcme uses Okta.",
                 "# Summary\n## מה למדתי\n- בקשות גישה בסלאק", "[00:20] them (en): We send access requests",
                 "[05:00] me (en): I will send you an invite for Tuesday"):
        assert part in text
    assert "בקשות: 3" not in text and "did not hear me" not in text


def test_the_input_says_when_callcoach_did_not_hear_me(tmp_path):
    rows = [*ROWS, {"kind": "hearing", "on": False, "t": STARTED + 60}, {"kind": "hearing", "on": True, "t": STARTED + 90}]
    data, folder = make_call(tmp_path, rows)
    assert "# When CallCoach did not hear me (I may have spoken then)\n- [01:00] to [01:30]" in \
        followup_message(data, folder)


def test_a_call_without_a_summary_or_a_brief_still_has_an_input(tmp_path):
    data, folder = make_call(tmp_path, meta={"brief": ""}, summary=None)
    text = followup_message(data, folder)
    assert "# Summary\n(none)" in text and "# Brief" not in text


def test_a_call_with_no_lines_has_nothing_to_write_from(tmp_path):
    data, folder = make_call(tmp_path, rows=[])
    with pytest.raises(FollowupError, match="אין תמלול"):
        followup_message(data, folder)


def test_the_call_language_is_the_language_of_most_of_their_words():
    assert call_language(ROWS) == "en"
    assert call_language([row(1, "them", "he", "שלום לכולם ותודה", 1), row(2, "them", "en", "ok", 5)]) == "he"
    assert call_language([row(1, "me", "en", "hello there", 1)]) == "en" and call_language([]) == "he"


def test_the_call_folder_is_a_name_in_calls_or_a_path(tmp_path):
    data, folder = make_call(tmp_path)
    assert call_dir(data, folder.name) == folder and call_dir(data, str(folder)) == folder
    with pytest.raises(FollowupError):
        call_dir(data, "missing")


@pytest.mark.parametrize("reply", [
    "Subject: Thanks for today\n\nHi Dana,\n\nThanks.\n",
    "**Subject:** Thanks for today\n\nHi Dana,\n\nThanks.",
    "```\nSubject: Thanks for today\nHi Dana,\n\nThanks.\n```",
    "Here is the draft:\nSUBJECT: Thanks for today\n\nHi Dana,\n\nThanks.",
])
def test_the_reply_becomes_a_subject_a_blank_line_and_the_body(reply):
    assert followup_text(reply) == "Subject: Thanks for today\n\nHi Dana,\n\nThanks.\n"


def test_a_hebrew_subject_line_is_read_too():
    assert followup_text("נושא: תודה על השיחה\n\nהיי דנה") == "Subject: תודה על השיחה\n\nהיי דנה\n"


@pytest.mark.parametrize("reply, message", [("Hi Dana,\nThanks.", "שורת נושא"), ("Subject: Thanks\n\n", "ריקה"),
                                            ("Subject:\n\nbody", "ריקה"), (None, "שורת נושא"),
                                            ("a\nb\nc\nSubject: late\n\nbody", "שורת נושא")])
def test_a_reply_that_is_not_an_email_is_refused(reply, message):
    with pytest.raises(FollowupError, match=message):
        followup_text(reply)


def test_followup_md_is_replaced_whole(tmp_path):
    write_followup(tmp_path, "Subject: a\n\nfirst\n")
    path = write_followup(tmp_path, "Subject: b\n\nsecond\n")
    assert path.read_text(encoding="utf-8") == "Subject: b\n\nsecond\n"
    assert [p.name for p in tmp_path.iterdir()] == ["followup.md"]


def run(tmp_path, monkeypatch, capsys, folder, provider=None):
    monkeypatch.setenv("CALLCOACH_PROVIDER", "fake")
    if provider is not None:
        monkeypatch.setattr("callcoach.provider_factory.build_provider", lambda account, key: provider)
    args = build_parser().parse_args(["followup", "--data", str(tmp_path / "data"), "--call", folder])
    code = cmd_followup(args)
    [line] = capsys.readouterr().out.splitlines()
    return code, json.loads(line)


def test_the_followup_command_parses():
    args = build_parser().parse_args(["followup", "--data", "d", "--call", "c"])
    assert (args.data, args.call) == ("d", "c")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["followup", "--data", "d"])


def test_the_followup_command_writes_the_draft(tmp_path, monkeypatch, capsys):
    data, folder = make_call(tmp_path)
    fake = FakeProvider(followup="Subject: Thanks for today\n\nHi Dana,\n\nThanks for your time.\n\nAlex")
    code, reply = run(tmp_path, monkeypatch, capsys, folder.name, fake)
    assert code == 0 and reply == {"ok": True, "path": str(folder / "followup.md")}
    assert (folder / "followup.md").read_text(encoding="utf-8") == \
        "Subject: Thanks for today\n\nHi Dana,\n\nThanks for your time.\n\nAlex\n"
    [(kind, message)] = fake.calls
    assert kind == "followup" and "# Transcript" in message and fake.closed


def test_the_followup_command_uses_the_followup_rules(tmp_path, monkeypatch, capsys):
    class Rules(FakeProvider):
        def followup(self, system, message):
            self.rules = system
            return super().followup(system, message)

    data, folder = make_call(tmp_path)
    fake = Rules()
    code, _ = run(tmp_path, monkeypatch, capsys, folder.name, fake)
    assert code == 0 and fake.rules == FOLLOWUP_RULES


def test_the_followup_command_works_with_the_fake_account(tmp_path, monkeypatch, capsys):
    data, folder = make_call(tmp_path)
    code, reply = run(tmp_path, monkeypatch, capsys, str(folder))
    assert code == 0 and (folder / "followup.md").read_text(encoding="utf-8").startswith("Subject: ")


def test_a_draft_written_again_replaces_the_old_one(tmp_path, monkeypatch, capsys):
    data, folder = make_call(tmp_path)
    (folder / "followup.md").write_text("Subject: old\n\nold\n", encoding="utf-8")
    code, _ = run(tmp_path, monkeypatch, capsys, folder.name, FakeProvider(followup="Subject: new\n\nnew"))
    assert code == 0 and (folder / "followup.md").read_text(encoding="utf-8") == "Subject: new\n\nnew\n"


@pytest.mark.parametrize("error, message", [(ProviderAuth("no login"), "החשבון נדחה: no login"),
                                            (ProviderIncomplete("max_tokens"), "המודל לא סיים לכתוב את הטיוטה"),
                                            (RuntimeError("boom"), "כתיבת הטיוטה נכשלה: boom")])
def test_a_failed_request_answers_not_ok_and_writes_nothing(tmp_path, monkeypatch, capsys, error, message):
    data, folder = make_call(tmp_path)
    fake = FakeProvider(errors=[error])
    code, reply = run(tmp_path, monkeypatch, capsys, folder.name, fake)
    assert code == 1 and reply["ok"] is False and reply["message"].startswith(message)
    assert not (folder / "followup.md").exists() and fake.closed


def test_a_reply_that_is_not_an_email_writes_nothing(tmp_path, monkeypatch, capsys):
    data, folder = make_call(tmp_path)
    code, reply = run(tmp_path, monkeypatch, capsys, folder.name, FakeProvider(followup="Hi Dana"))
    assert (code, reply["ok"]) == (1, False) and "שורת נושא" in reply["message"]
    assert not (folder / "followup.md").exists()


def test_no_account_or_no_call_answers_not_ok(tmp_path, monkeypatch, capsys):
    data, folder = make_call(tmp_path)
    monkeypatch.setenv("CALLCOACH_PROVIDER", "none")
    args = build_parser().parse_args(["followup", "--data", str(data), "--call", folder.name])
    assert cmd_followup(args) == 1
    assert json.loads(capsys.readouterr().out)["message"] == "אין חשבון פעיל: צריך לבחור חשבון בהגדרות"
    code, reply = run(tmp_path, monkeypatch, capsys, "missing")
    assert code == 1 and reply == {"ok": False, "message": "לא מצאתי את תיקיית השיחה"}


def test_the_followup_rules_never_send_and_never_invent():
    assert "Nothing is sent" in FOLLOWUP_RULES and "never invent" in FOLLOWUP_RULES
    assert 'The first line is "Subject: "' in FOLLOWUP_RULES
