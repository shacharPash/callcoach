from datetime import datetime

import pytest

from callcoach.cards import Question
from callcoach.config import DEFAULTS_DIR
from callcoach.context import (CallType, Goal, Prep, address_form, address_of, brief_message, build_prefix,
                               call_type_names, ensure_data, knowledge_files, load_call_type, opening_questions,
                               parse_call_type, prep_message, read_brief_terms, read_briefs, research_message)
from callcoach.prompts import STEP_RULES


def test_a_call_type_file_is_parsed():
    ct = parse_call_type("validation", "library: mom-test, extra\ncadence: call\ncards: question, tip\n"
                                       "goals:\n- One\n- Two\n---\nBe brief.\n")
    assert ct == CallType("validation", ("mom-test", "extra"), ("question", "tip"), ("One", "Two"), "Be brief.")


def test_an_old_watch_call_type_is_an_ordinary_call_type():
    ct = parse_call_type("watch", "cadence: watch\ncards: term\n---\nI am only listening.\n")
    assert ct == CallType("watch", cards=("term",), guidance="I am only listening.")


def test_the_default_call_types_load(tmp_path):
    ensure_data(tmp_path)
    assert load_call_type(tmp_path, "validation").library == ("mom-test",)
    assert not (tmp_path / "call-types" / "watch.md").exists() and load_call_type(tmp_path, "watch") == CallType("watch")
    assert load_call_type(tmp_path, "general").goals == ()
    assert load_call_type(tmp_path, "nope") == CallType("nope")


def test_ensure_data_never_overwrites(tmp_path):
    (tmp_path / "me.md").write_text("mine", encoding="utf-8")
    ensure_data(tmp_path)
    assert (tmp_path / "me.md").read_text(encoding="utf-8") == "mine"
    assert (tmp_path / "library" / "mom-test.md").exists() and (tmp_path / "briefs").is_dir()


def test_brief_names_cannot_leave_the_briefs_folder(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "briefs" / "acme.md").write_text("Acme notes", encoding="utf-8")
    (tmp_path / "secret.md").write_text("no", encoding="utf-8")
    assert read_briefs(tmp_path, ["acme.md", "../secret.md", ".hidden", "missing.md", ""]) == [("acme.md", "Acme notes")]


def test_a_brief_is_read_without_its_header(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "briefs" / "acme.md").write_text("---\ntitle: Acme\ngoals:\n  - one\n---\n## רקע\nAcme uses Okta.\n",
                                                encoding="utf-8")
    assert read_briefs(tmp_path, ["acme.md"]) == [("acme.md", "## רקע\nAcme uses Okta.")]


def test_a_brief_header_the_app_reads_is_never_in_the_body(tmp_path):
    ensure_data(tmp_path)
    text = ("\ufeff\r\n\r\n---\r\ntitle: 'Acme'\r\ngoals: [one, two]\r\nglossary:\r\n  - Okta\r\n---\r\n"
            "## רקע\r\nAcme.\r\n")
    (tmp_path / "briefs" / "acme.md").write_bytes(text.encode("utf-8"))
    assert read_briefs(tmp_path, ["acme.md"]) == [("acme.md", "## רקע\nAcme.")]


def make_prep(**kw):
    base = dict(call_type="validation", text="דנה מ-Acme", brief="acme.md", length_min=30,
                goals=(Goal("g1", "להבין איך מאשרים גישה"),))
    return Prep(**{**base, **kw})


def test_the_prefix_holds_every_layer(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "me.md").write_text("I build access tools.", encoding="utf-8")
    (tmp_path / "briefs" / "acme.md").write_text("Acme uses Okta.", encoding="utf-8")
    prep = make_prep()
    text = build_prefix(tmp_path, prep, load_call_type(tmp_path, "validation"))
    for part in (STEP_RULES.strip()[:40], "I build access tools.", "# Library: mom-test", "Customer validation",
                 "allowed_cards: question, tip, steer", "דנה מ-Acme", "planned length: 30 min",
                 "Acme uses Okta.", "[g1] להבין איך מאשרים גישה"):
        assert part in text
    assert "# Glossary" not in text
    with_glossary = build_prefix(tmp_path, make_prep(glossary=("SOC 2", "Okta")), load_call_type(tmp_path, "validation"))
    assert "# Glossary\nSOC 2, Okta" in with_glossary


def test_an_old_call_type_that_allows_term_cards_does_not_offer_them(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "call-types" / "validation.md").write_text("cadence: call\ncards: question, term\n---\nx\n",
                                                           encoding="utf-8")
    text = build_prefix(tmp_path, make_prep(), load_call_type(tmp_path, "validation"))
    assert "allowed_cards: question\n" in text


def test_a_call_type_with_only_terms_offers_no_cards(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "call-types" / "listen.md").write_text("cards: term\n---\nI am only listening.\n", encoding="utf-8")
    text = build_prefix(tmp_path, make_prep(), load_call_type(tmp_path, "listen"))
    assert "allowed_cards: (none)\n" in text and "cadence" not in text


def test_the_rules_ask_for_more_terms():
    assert "At most 5 terms per answer" in STEP_RULES and "When in doubt, include it" in STEP_RULES
    assert '"now"' in STEP_RULES and "shown_now" in STEP_RULES and "now off: now is" in STEP_RULES


def test_the_prep_message_holds_the_notes_and_the_goal_template(tmp_path):
    ensure_data(tmp_path)
    text = prep_message(tmp_path, make_prep(brief=""), load_call_type(tmp_path, "validation"))
    assert "דנה מ-Acme" in text and "- למצוא את הפעם האחרונה שזה כאב, עם דוגמה אמיתית" in text
    assert "call language: he" in text


def test_the_rules_set_the_card_language_and_the_address():
    assert "card_language" in STEP_RULES and "Address me as:" in STEP_RULES


def test_term_notes_are_always_in_hebrew():
    assert "note: what it is, at most 8 words, in Hebrew" in STEP_RULES


def test_prep_from_a_start_command():
    cmd = {"type": "start", "call_type": "sales", "lang": "en", "level": "quiet", "text": "x", "brief": "acme.md",
           "length_min": None, "goals": [{"id": "g1", "text": "t", "done": False}], "glossary": ["Okta"],
           "name": "Dana", "title": " שיחה עם Acme ", "questions": ["א?", " ", "ב?", "ג?", "ד?"], "replay": "demo.wav"}
    p = Prep.from_command(cmd)
    assert (p.call_type, p.lang, p.level, p.goals, p.glossary, p.name, p.replay) == \
        ("sales", "en", "quiet", (Goal("g1", "t"),), ("Okta",), "Dana", "demo.wav")
    assert (p.brief, p.title, p.first_lang) == ("acme.md", "שיחה עם Acme", "en")
    assert p.questions == (Question("א?"), Question("ב?"), Question("ג?"))


def test_opening_questions_split_their_translation():
    items = ["What made you look now? | מה גרם לכם לחפש עכשיו?", "Who decides?", "מי מאשר? | מי מאשר?",
             " | רק תרגום", "fourth?"]
    assert opening_questions(items) == (Question("What made you look now?", "מה גרם לכם לחפש עכשיו?"),
                                        Question("Who decides?"), Question("מי מאשר?"))


def test_a_start_command_with_translated_questions():
    cmd = {"type": "start", "call_type": "sales", "lang": "en", "level": "normal", "text": "", "brief": "",
           "length_min": None, "title": "", "questions": ["Why now? | למה עכשיו?"]}
    assert Prep.from_command(cmd).questions == (Question("Why now?", "למה עכשיו?"),)


def test_an_auto_language_starts_in_hebrew():
    assert Prep(lang="auto").first_lang == "he" and Prep(lang="en").first_lang == "en"


def test_the_call_types_are_the_defaults_and_my_own(tmp_path):
    assert call_type_names(tmp_path) == ["general", "sales", "validation"]
    (tmp_path / "call-types").mkdir()
    (tmp_path / "call-types" / "board.md").write_text("cadence: call\n---\nA board meeting. Keep it short.\n",
                                                      encoding="utf-8")
    assert "board" in call_type_names(tmp_path)


def test_the_brief_message_holds_the_request_me_the_call_types_and_the_knowledge(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "me.md").write_text("Who I am: a founder\n", encoding="utf-8")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "people.md").write_text("## Dana\nסוג: אדם\nתקציר: מנהלת IT\n", encoding="utf-8")
    (tmp_path / "knowledge" / "people.backup.md").write_text("old", encoding="utf-8")
    request = {"title": "", "when": "מחר 14:00", "text": "שיחה עם דנה מ-Acme", "links": ["https://example.com"],
               "files": [{"name": "notes.txt", "text": "Acme uses Okta"}]}
    text = brief_message(tmp_path, request, "## Company\nAcme makes widgets", datetime(2026, 11, 16, 9, 30))
    for part in ("now: 2026-11-16 09:30 (Monday)", "title: (none)", "when: מחר 14:00", "שיחה עם דנה מ-Acme",
                 "## File: notes.txt\nAcme uses Okta", "- https://example.com", "Who I am: a founder",
                 "# Research notes\n## Company\nAcme makes widgets",
                 "- sales: Sales discovery.",
                 '<knowledge file="people">\n## Dana'):
        assert part in text
    assert "old" not in text and "- watch:" not in text
    assert "# Research notes\n(none)" in brief_message(tmp_path, request, " ", datetime(2026, 11, 16, 9, 30))


def test_the_research_is_given_the_request_alone(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "me.md").write_text("Who I am: a founder\n", encoding="utf-8")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "people.md").write_text("## Dana\nתקציר: מנהלת IT\n", encoding="utf-8")
    request = {"title": "שיחה עם Acme", "when": "", "text": "שיחה עם דנה מ-Acme", "links": ["https://example.com"],
               "files": [{"name": "notes.txt", "text": "Acme uses Okta"}]}
    text = research_message(request)
    assert text == ("# Request\ntitle: שיחה עם Acme\nwhen: (none)\n\n## What the call is about\nשיחה עם דנה מ-Acme\n\n"
                    "## File: notes.txt\nAcme uses Okta\n\n## Links\n- https://example.com\n")


def test_a_knowledge_file_that_is_not_utf8_is_left_out(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "a.md").write_bytes(b"\xff\xfe broken \x80")
    (tmp_path / "knowledge" / "b.md").write_text("## Dana", encoding="utf-8")
    assert knowledge_files(tmp_path) == [("b", "## Dana")]
    request = {"title": "", "when": "", "text": "x", "links": [], "files": []}
    assert '<knowledge file="b">' in brief_message(tmp_path, request, "", datetime(2026, 11, 16, 9, 30))


def test_a_local_only_knowledge_file_is_never_sent(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "a.md").write_text("# ידע\nלמודל: לא\n\n## Dana\nתקציר: מנהלת IT\n", encoding="utf-8")
    (tmp_path / "knowledge" / "b.md").write_text("## Wiz\nתקציר: אבטחת ענן\nלמודל: לא\n", encoding="utf-8")
    assert [name for name, _ in knowledge_files(tmp_path)] == ["b"]
    assert knowledge_files(tmp_path, limit=10) == [("b", "## Wiz\nתקצ")]
    request = {"title": "", "when": "", "text": "x", "links": [], "files": []}
    text = brief_message(tmp_path, request, "", datetime(2026, 11, 16, 9, 30))
    assert '<knowledge file="b">' in text and "Dana" not in text and '<knowledge file="a">' not in text


def test_the_knowledge_files_are_cut_in_all(tmp_path):
    (tmp_path / "knowledge").mkdir()
    for name in ("a", "b", "c"):
        (tmp_path / "knowledge" / f"{name}.md").write_text(name * 30, encoding="utf-8")
    assert knowledge_files(tmp_path, limit=50) == [("a", "a" * 30), ("b", "b" * 20)]
    assert knowledge_files(tmp_path / "none") == []


def test_the_default_me_leaves_the_address_empty():
    text = (DEFAULTS_DIR / "me.md").read_text(encoding="utf-8")
    assert "Address me as:" in text.splitlines() and address_form(text) == ""


@pytest.mark.parametrize("text, form", [
    ("Address me as: masculine", "masculine"),
    ("Who I am: a founder\naddress me as:   feminine  ", "feminine"),
    ("Address me as:\n<!-- masculine or feminine -->", ""),
    ("Address me as: (masculine or feminine; Hebrew cards use this form)", ""),
    ("Who I am: a founder", ""),
])
def test_the_address_form_is_read_from_me(text, form):
    assert address_form(text) == form


def test_an_empty_address_reaches_the_agent_as_not_given(tmp_path):
    ensure_data(tmp_path)
    call_type = load_call_type(tmp_path, "validation")
    prefix = build_prefix(tmp_path, make_prep(), call_type)
    me = prefix[prefix.index("# Me"):prefix.index("# Library")]
    assert "Address me as: not given" in me and "<!--" not in me and "masculine or feminine" not in me
    assert "Address me as: not given" in prep_message(tmp_path, make_prep(), call_type)
    assert address_of(tmp_path) == ""


def test_a_given_address_reaches_the_agent(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "me.md").write_text("Who I am: a founder\nAddress me as: masculine\n", encoding="utf-8")
    call_type = load_call_type(tmp_path, "validation")
    for text in (build_prefix(tmp_path, make_prep(), call_type), prep_message(tmp_path, make_prep(), call_type)):
        assert "Address me as: masculine" in text
    assert address_of(tmp_path) == "masculine"


def test_the_terms_of_a_brief_are_read_from_its_header(tmp_path):
    ensure_data(tmp_path)
    (tmp_path / "briefs" / "acme.md").write_text(
        "---\ntitle: Acme\nterms:\n  - SOC 2: תקן ביקורת אבטחה\n  - Nimbus Labs: המתחרה\n  - soc 2: שוב\n"
        "  - No note\n---\n## רקע\nx\n", encoding="utf-8")
    assert read_brief_terms(tmp_path, "acme.md") == (("SOC 2", "תקן ביקורת אבטחה"), ("Nimbus Labs", "המתחרה"))
    assert read_brief_terms(tmp_path, "missing.md") == () and read_brief_terms(tmp_path, "../acme.md") == ()
    assert read_brief_terms(tmp_path, "") == ()


def test_the_defaults_have_no_watch_and_no_cadence():
    assert not (DEFAULTS_DIR / "call-types" / "watch.md").exists()
    for path in (DEFAULTS_DIR / "call-types").glob("*.md"):
        assert "cadence" not in path.read_text(encoding="utf-8"), path.name
