from types import SimpleNamespace

import pytest

from callcoach.providers import FakeProvider, ProviderUnavailable, Usage, demo_reply


def test_usage_adds_up_and_reads_api_fields():
    u = Usage.from_api({"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 900,
                        "cache_creation_input_tokens": 50})
    v = Usage.from_api(SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=None,
                                       cache_creation_input_tokens=0))
    assert u + v == Usage(11, 4, 900, 50, 2)
    assert u.to_dict() == {"input": 10, "output": 3, "cache_read": 900, "cache_write": 50, "requests": 1}


def test_the_fake_replays_a_script_and_records_calls():
    fake = FakeProvider([{"card": None, "goals": []}])
    fake.open_call("system", {})
    assert fake.step("hello").data == {"card": None, "goals": []}
    assert fake.step("again").data == {"card": None, "goals": [], "terms": []}
    assert [c[0] for c in fake.calls] == ["step", "step"] and fake.system == "system"


def test_the_fake_raises_scripted_errors_in_order():
    fake = FakeProvider([], errors=[ProviderUnavailable("down"), None])
    with pytest.raises(ProviderUnavailable):
        fake.step("a")
    assert fake.step("b").data == {"card": None, "goals": [], "terms": []}


def test_the_demo_replies_react_to_the_planted_moments():
    tip = demo_reply("## New lines\n[00:24] them (he): זה נשמע לי רעיון מעולה, בטוח הייתי משתמש בכלי כזה.\n## State\nmode: normal")
    forced = demo_reply("## New lines\n(none)\n## State\nmode: forced_question")
    quiet = demo_reply("## New lines\n[00:01] them (he): שלום\n## State\nmode: normal")
    assert tip["card"]["kind"] == "tip" and forced["card"]["kind"] == "question" and quiet["card"] is None
    term = demo_reply("## New lines\n[00:30] me (he): איך אתם עושים את הביקורת של SOC 2?\n## State\nlevel: quiet")
    assert term["card"] is None and [t["term"] for t in term["terms"]] == ["SOC 2"]


def test_the_demo_offers_live_questions_only_when_they_are_on():
    from callcoach.providers import demo_reply
    on = demo_reply("[00:01] them (he): זה די בלגן אצלנו\n## State\nmode: normal\nlive_questions: on\n")
    off = demo_reply("[00:01] them (he): זה די בלגן אצלנו\n## State\nmode: normal\nlive_questions: off\n")
    assert [q["text"] for q in on["questions"]] == ["מתי זה יצר לכם בעיה?", "מי מאשר בקשה כזאת?"]
    assert off["questions"] == []


STATE = "## State\nmode: normal\nlive_questions: on\nsections: now {}, ask {}, terms {}\nhearing_me: {}\n"


def demo(line, now="on", ask="on", terms="on", hearing="on"):
    return demo_reply(f"## New lines\n[00:01] them (en): {line}\n" + STATE.format(now, ask, terms, hearing))


def test_the_demo_answers_in_english_with_translations():
    reply = demo("We bought Nimbus Labs and nobody uses it. How is yours different?")
    assert reply["card"] == {"kind": "question", "text": "What would make you switch?", "lang": "en",
                             "he": "מה יגרום לכם לעבור?"}
    assert reply["questions"][0] == {"text": "Why does nobody use it?", "he": "למה אף אחד לא משתמש בו?"}
    assert reply["asked"] == {"text": "How is yours different?", "he": "במה שלכם שונה?"}


def test_the_demo_leaves_closed_sections_empty():
    line = "We bought Nimbus Labs and nobody uses it. How is yours different?"
    closed = demo(line, ask="off")
    assert (closed["card"], closed["questions"], closed["asked"]) == (None, [], None)
    assert demo(line, terms="off")["terms"] == [] and demo(line)["terms"]
    assert demo(line, hearing="off")["asked"] is None


def test_the_fake_recaps_and_drafts_a_followup():
    from callcoach.prompts import FOLLOWUP_RULES, RECAP_RULES

    fake = FakeProvider(recap="שורה אחת", followup="Subject: x\n\nbody")
    assert fake.recap(RECAP_RULES, "## Transcript").data == "שורה אחת"
    assert fake.explain("rules", "term: Okta").data.startswith("שירות")
    assert fake.followup(FOLLOWUP_RULES, "# Transcript").data == "Subject: x\n\nbody"
    assert [kind for kind, _ in fake.calls] == ["recap", "explain", "followup"]
    assert FakeProvider().followup("rules", "x").data.startswith("Subject: ")


def test_the_fake_explains_researches_and_writes_a_brief():
    from callcoach.brief import parse_brief

    fake = FakeProvider()
    assert fake.explain("rules", "term: Okta").data.startswith("שירות")
    assert "## מקורות" in fake.research("rules", "request").data
    header, body = parse_brief(fake.brief("rules", "request").data)
    assert header["title"] and "## לא לשכוח" in body
    assert [kind for kind, _ in fake.calls] == ["explain", "research", "brief"]
