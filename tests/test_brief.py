import io
import json
from datetime import date

import pytest

from callcoach.__main__ import build_parser, cmd_brief
from callcoach.brief import (MAX_TERMS, BriefError, brief_terms, brief_title, normalize, parse_brief, question_parts,
                             read_request, render_brief, reply_text, save_reply, term_parts, write_brief)
from callcoach.providers import FakeProvider, ProviderAuth, ProviderIncomplete, ProviderUnavailable

TYPES = ("general", "sales", "validation")
EXAMPLE = """---
title: שיחת היכרות עם Acme
when: 2026-11-17 14:00
type: sales
lang: en
length: 30
goals:
  - להבין איך הם מנהלים היום הרשאות
  - לקבוע פגישת המשך
questions:
  - מה גרם לכם לחפש פתרון עכשיו?
glossary: Acme, Okta, SOC 2
---
## מי בשיחה
## רקע
## לא לשכוח
"""


def test_the_header_and_the_body_are_read():
    header, body = parse_brief(EXAMPLE)
    assert header == {"title": "שיחת היכרות עם Acme", "when": "2026-11-17 14:00", "type": "sales", "lang": "en",
                      "length": "30", "goals": ["להבין איך הם מנהלים היום הרשאות", "לקבוע פגישת המשך"],
                      "questions": ["מה גרם לכם לחפש פתרון עכשיו?"], "glossary": ["Acme", "Okta", "SOC 2"]}
    assert body == "## מי בשיחה\n## רקע\n## לא לשכוח"


def test_the_example_is_written_back_exactly():
    header, body = parse_brief(EXAMPLE)
    assert render_brief(normalize(header, TYPES), body) == EXAMPLE


@pytest.mark.parametrize("text", ["# Acme\nAcme uses Okta.\n", "---\ntitle: never closed\n", "", "-- \nx"])
def test_a_file_with_no_header_is_all_body(text):
    assert parse_brief(text) == ({}, text.strip())


def test_unknown_keys_and_blank_lines_are_ignored():
    header, body = parse_brief("﻿---\nTitle: Acme\nowner: someone\n\ngoals:\n\n  - one\n  -\n  - two: yes\n"
                               "notes:\n  - not a list key\n---\nbody\n")
    assert header == {"title": "Acme", "goals": ["one", "two: yes"]} and body == "body"


def test_a_header_is_read_as_leniently_as_the_app_reads_it():
    text = ("\ufeff\r\n  \r\n---\r\ntitle: \"שיחה עם Acme\"\r\ntype: 'Sales'\r\nlang: EN\r\nlength: 45 דקות\r\n"
            "goals: [להבין את התקציב, 'לקבוע המשך']\r\nquestions: [\"מה חשוב לכם?\"]\r\nglossary:\r\n  - Okta\r\n"
            "  - 'SOC 2'\r\n---\r\n## רקע\r\nAcme.\r\n")
    header, body = parse_brief(text)
    assert body == "## רקע\nAcme."
    assert normalize(header, TYPES) == {"title": "שיחה עם Acme", "type": "sales", "lang": "en", "length": 45.0,
                                        "goals": ["להבין את התקציב", "לקבוע המשך"], "questions": ["מה חשוב לכם?"],
                                        "glossary": ["Okta", "SOC 2"]}


@pytest.mark.parametrize("lines, expected", [
    ("goals: one goal", {"goals": ["one goal"]}),
    ("goals: [a, b]\n  - c", {"goals": ["c"]}),
    ("glossary: [Okta, SOC 2]", {"glossary": ["Okta", "SOC 2"]}),
    ("glossary: Okta, 'SOC 2'", {"glossary": ["Okta", "SOC 2"]}),
    ("glossary:\n  - Okta, Inc.", {"glossary": ["Okta, Inc."]}),
    ("- before any key\ntitle: x", {"title": "x"}),
    ("title: 'Acme\"", {"title": "'Acme\""}),
    ("title: \"\"", {"title": ""}),
])
def test_the_list_and_quote_forms(lines, expected):
    assert parse_brief(f"---\n{lines}\n---\nbody")[0] == expected


def test_the_title_comes_from_the_header_the_first_heading_or_the_file_name():
    assert brief_title({"title": "Acme"}, "# Other", "x.md") == "Acme"
    assert brief_title({}, "intro\n# שיחה עם Acme\n## רקע", "x.md") == "שיחה עם Acme"
    assert brief_title({}, "no heading", "2026-11-17-acme.md") == "2026-11-17-acme"


@pytest.mark.parametrize("header, expected", [
    ({}, {"type": "general", "lang": "auto"}),
    ({"type": "board", "lang": "fr"}, {"type": "general", "lang": "auto"}),
    ({"type": "sales", "lang": "HE"}, {"type": "sales", "lang": "he"}),
    ({"type": "watch"}, {}),
    ({"when": "2026-11-17 14:00"}, {"when": "2026-11-17 14:00"}),
    ({"when": "tomorrow at 2"}, {}),
    ({"when": "2026-13-01 14:00"}, {}),
    ({"when": "2026-11-17"}, {}),
    ({"length": "45"}, {"length": 45.0}),
    ({"length": "22.5"}, {"length": 22.5}),
    ({"length": "0"}, {}),
    ({"length": "-5"}, {}),
    ({"length": "30 דקות"}, {"length": 30.0}),
    ({"length": "600"}, {"length": 600.0}),
    ({"length": "601"}, {}),
    ({"length": "nan"}, {}),
    ({"length": "1.2.3"}, {}),
    ({"type": " Sales "}, {"type": "sales"}),
    ({"title": "  שיחה   עם Acme "}, {"title": "שיחה עם Acme"}),
])
def test_the_header_is_checked(header, expected):
    assert normalize(header, TYPES) == {"type": "general", "lang": "auto", **expected}


def test_the_lists_are_cut():
    header = {"goals": [f"מטרה {i}" for i in range(8)], "questions": ["א?", "ב?", "", "ג?", "ד?"],
              "glossary": ["Okta", " ", "SOC  2"]}
    out = normalize(header, TYPES)
    assert out["goals"] == [f"מטרה {i}" for i in range(5)] and out["questions"] == ["א?", "ב?", "ג?"]
    assert out["glossary"] == ["Okta", "SOC 2"]
    assert "goals" not in normalize({"goals": []}, TYPES)


def test_a_brief_is_named_by_its_day_and_title_and_never_replaces_one(tmp_path):
    header = normalize({"title": "שיחת היכרות עם Acme!", "when": "2026-11-17 14:00"}, TYPES)
    paths = [write_brief(tmp_path, header, "## רקע", today=date(2026, 11, 16)) for _ in range(3)]
    assert [p.name for p in paths] == ["2026-11-17-שיחת-היכרות-עם-Acme.md", "2026-11-17-שיחת-היכרות-עם-Acme-2.md",
                                       "2026-11-17-שיחת-היכרות-עם-Acme-3.md"]
    assert paths[0].read_text(encoding="utf-8").startswith("---\ntitle: שיחת היכרות עם Acme!\nwhen: 2026-11-17 14:00\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in paths)


def test_a_brief_without_when_or_title_is_named_by_today(tmp_path):
    path = write_brief(tmp_path / "briefs", normalize({}, TYPES), "plain", today=date(2026, 11, 16))
    assert path.name == "2026-11-16-brief.md"
    assert path.read_text(encoding="utf-8") == "---\ntype: general\nlang: auto\n---\nplain\n"


def request(**kw):
    return json.dumps({"text": "שיחה עם דנה מ-Acme", **kw}, ensure_ascii=False)


def test_a_request_is_read_and_checked():
    out = read_request(request(title=" שיחה ", files=[{"name": "a.txt", "text": "x"}], links=[" https://a.test ", ""]))
    assert out == {"title": "שיחה", "when": "", "text": "שיחה עם דנה מ-Acme", "files": [{"name": "a.txt", "text": "x"}],
                   "links": ["https://a.test"]}
    assert read_request('{"text": "x", "title": null, "files": null}')["files"] == []


@pytest.mark.parametrize("raw", [
    "not json", "[]", "{}", '{"text": 5}', request(files="a.txt"), request(files=[{"name": "a.txt"}]),
    request(links=[1]), request(when=5), '{"text": " ", "files": [], "links": []}',
])
def test_a_bad_request_is_a_hebrew_error(raw):
    with pytest.raises(BriefError) as e:
        read_request(raw)
    assert "֐" <= str(e.value)[0] <= "׿"


def test_the_brief_starts_at_its_first_header_line():
    assert reply_text("Here it is:\n```markdown\n---\ntitle: x\n---\nbody\n```\n") == "---\ntitle: x\n---\nbody"
    assert reply_text("no header at all") == "no header at all" and reply_text(None) == ""


def test_the_request_title_and_when_take_the_place_of_the_models(tmp_path):
    reply = "---\ntitle: model title\nwhen: 2026-11-20 10:00\ntype: nope\n---\n## רקע\nחברה לדוגמה\n"
    req = read_request(request(title="שיחה עם Acme", when="2026-11-18 09:30"))
    path = save_reply(tmp_path, reply, req, TYPES, today=date(2026, 11, 16))
    assert path.name == "2026-11-18-שיחה-עם-Acme.md"
    header, body = parse_brief(path.read_text(encoding="utf-8"))
    assert header == {"title": "שיחה עם Acme", "when": "2026-11-18 09:30", "type": "general", "lang": "auto"}
    assert body == "## רקע\nחברה לדוגמה"
    with pytest.raises(BriefError):
        save_reply(tmp_path, "  ", req, TYPES)


def run_brief(tmp_path, monkeypatch, capsys, stdin, provider="fake"):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    if provider == "fake":
        monkeypatch.setenv("CALLCOACH_PROVIDER", "fake")
    else:
        monkeypatch.setattr("callcoach.provider_factory.build_provider", lambda account, key: provider)
    code = cmd_brief(build_parser().parse_args(["brief", "--data", str(tmp_path / "data")]))
    [line] = capsys.readouterr().out.splitlines()
    return code, json.loads(line)


def test_the_brief_command_writes_a_brief(tmp_path, monkeypatch, capsys):
    code, reply = run_brief(tmp_path, monkeypatch, capsys, request(links=["https://example.com/acme"]))
    assert code == 0 and reply["ok"] is True
    path = tmp_path / "data" / "briefs" / reply["path"].rsplit("/", 1)[1]
    assert reply["path"] == str(path) and path.name.endswith("-שיחת-היכרות-עם-Acme.md")
    header, body = parse_brief(path.read_text(encoding="utf-8"))
    assert header["type"] == "validation" and header["questions"] == ["איך אתם מנהלים היום בקשות גישה לענן?"]
    assert "## מי בשיחה" in body


def with_data(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "people.md").write_text("## Dana\nתקציר: מנהלת IT\n", encoding="utf-8")
    (tmp_path / "data" / "me.md").write_text("Who I am: a founder\n", encoding="utf-8")


def test_the_research_has_the_request_alone_and_the_writing_has_no_web(tmp_path, monkeypatch, capsys):
    fake = FakeProvider(research="## Company\nAcme makes widgets")
    with_data(tmp_path)
    code, reply = run_brief(tmp_path, monkeypatch, capsys, request(links=["https://example.com/acme"]), fake)
    assert code == 0 and reply["ok"]
    assert [kind for kind, _ in fake.calls] == ["research", "brief"]
    [researched, written] = [message for _, message in fake.calls]
    for message in (researched, written):
        assert "שיחה עם דנה מ-Acme" in message and "- https://example.com/acme" in message
    assert "founder" not in researched and "knowledge" not in researched and "מנהלת IT" not in researched
    assert "# Research notes\n## Company\nAcme makes widgets" in written and "Who I am: a founder" in written
    assert '<knowledge file="people">' in written and "- sales:" in written and fake.closed


def test_a_failed_research_still_writes_the_brief_and_says_so(tmp_path, monkeypatch, capsys):
    fake = FakeProvider(errors=[ProviderIncomplete("the model stopped with max_tokens")])
    monkeypatch.setattr("sys.stdin", io.StringIO(request()))
    monkeypatch.setattr("callcoach.provider_factory.build_provider", lambda account, key: fake)
    code = cmd_brief(build_parser().parse_args(["brief", "--data", str(tmp_path / "data")]))
    out, err = capsys.readouterr()
    assert code == 0 and json.loads(out)["ok"] is True
    assert json.loads(err) == {"note": "research_failed", "error": "ProviderIncomplete",
                               "message": "the model stopped with max_tokens"}
    assert [kind for kind, _ in fake.calls] == ["research", "brief"]
    assert "# Research notes\n(the research failed" in fake.calls[1][1]


def test_bad_json_is_answered_with_ok_false(tmp_path, monkeypatch, capsys):
    code, reply = run_brief(tmp_path, monkeypatch, capsys, "{not json")
    assert code == 1 and reply == {"ok": False, "message": "הבקשה אינה JSON תקין"}
    assert not (tmp_path / "data" / "briefs").exists()


@pytest.mark.parametrize("provider, word", [
    (None, "אין חשבון"),
    (FakeProvider(errors=[ProviderAuth("invalid key")]), "החשבון נדחה"),
    (FakeProvider(errors=[None, ProviderAuth("invalid key")]), "החשבון נדחה"),
    (FakeProvider(errors=[None, ProviderUnavailable("busy")]), "נכשלה"),
    (FakeProvider(errors=[None, ProviderIncomplete("the model stopped with max_tokens")]), "לא סיים"),
    (FakeProvider(brief=""), "ריקה"),
])
def test_a_failed_brief_is_answered_with_ok_false(tmp_path, monkeypatch, capsys, provider, word):
    code, reply = run_brief(tmp_path, monkeypatch, capsys, request(), provider)
    assert code == 1 and reply["ok"] is False and word in reply["message"]
    assert not (tmp_path / "data" / "briefs").exists() or not any((tmp_path / "data" / "briefs").glob("*.md"))


TRANSLATED = """---
title: Intro call with Acme
type: sales
lang: en
questions:
  - What made you look now? | מה גרם לכם לחפש עכשיו?
  - Who decides?
glossary: Acme, Okta
terms:
  - SOC 2: תקן ביקורת אבטחה לספקי שירותים
  - Nimbus Labs: המתחרה שהם קנו בשנה שעברה
---
## רקע
"""


def test_translations_and_terms_are_written_back_exactly():
    header, body = parse_brief(TRANSLATED)
    assert header["terms"] == ["SOC 2: תקן ביקורת אבטחה לספקי שירותים", "Nimbus Labs: המתחרה שהם קנו בשנה שעברה"]
    assert render_brief(normalize(header, TYPES), body) == TRANSLATED


def test_a_question_splits_at_its_translation():
    assert question_parts("What made you look now? | מה גרם לכם לחפש עכשיו?") == ("What made you look now?",
                                                                                 "מה גרם לכם לחפש עכשיו?")
    assert question_parts("  Who  decides? ") == ("Who decides?", "")
    assert question_parts("A|B") == ("A|B", "") and question_parts(" | רק תרגום") == ("", "")
    assert question_parts("Who decides? |") == ("Who decides?", "")


def test_a_term_splits_at_its_first_colon():
    assert term_parts("SOC 2: תקן: ביקורת") == ("SOC 2", "תקן: ביקורת")
    assert term_parts("No note") == ("", "") and term_parts(": רק הערה") == ("", "")
    assert len(term_parts("X: " + " ".join(["מילה"] * 20))[1].split()) == 12


def test_the_terms_are_cut_and_each_name_kept_once():
    header = {"terms": ["SOC 2: תקן", "soc 2: שוב", "Okta", *(f"T{i}: הערה" for i in range(30))]}
    terms = brief_terms(header)
    assert terms[0] == ("SOC 2", "תקן") and len(terms) == MAX_TERMS and ("Okta", "") not in terms
    assert normalize(header, TYPES)["terms"][:2] == ["SOC 2: תקן", "T0: הערה"]
    assert "terms" not in normalize({"terms": ["no note"]}, TYPES)


def test_the_questions_keep_their_translation_through_normalize():
    out = normalize({"questions": ["Why now?  |  למה עכשיו?", "Who? | ", "", "מה?", "ד?"]}, TYPES)
    assert out["questions"] == ["Why now? | למה עכשיו?", "Who?", "מה?"]


def test_the_demo_brief_has_terms():
    header, _ = parse_brief(FakeProvider().brief("rules", "request").data)
    assert brief_terms(header)


def test_a_brief_the_app_page_wrote_reads_the_same_and_its_own_keys_are_ignored():
    text = ("---\ntitle: שיחת היכרות עם דנה\nwhen: 2026-11-19 14:00\ntype: sales\nlang: he\nlength: 30\n"
            "goals:\n  - להבין מי מאשר רכישה\nquestions:\n  - What made you look now? | מה גרם לכם לחפש עכשיו?\n"
            "glossary: Okta, SCIM\nsource: drive\nterms:\n  - SCIM: תקן לסנכרון משתמשים\n"
            "links:\n  - https://example.com/acme\nafter: 2026-11-18_1131_general\n---\n## מי בשיחה\nדנה, מנהלת IT\n")
    header, body = parse_brief(text)
    assert header == {"title": "שיחת היכרות עם דנה", "when": "2026-11-19 14:00", "type": "sales", "lang": "he",
                      "length": "30", "goals": ["להבין מי מאשר רכישה"],
                      "questions": ["What made you look now? | מה גרם לכם לחפש עכשיו?"], "glossary": ["Okta", "SCIM"],
                      "terms": ["SCIM: תקן לסנכרון משתמשים"]}
    assert body == "## מי בשיחה\nדנה, מנהלת IT"
    assert brief_terms(header) == (("SCIM", "תקן לסנכרון משתמשים"),)
