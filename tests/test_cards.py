import json

import pytest

from callcoach.cards import (MAX_NOW_WORDS, LONG_CARD_WORDS, MAX_ASKED_WORDS, MAX_HE_WORDS, MAX_QUESTION_WORDS,
                             MAX_TERMS, NUDGE_WORDS, OFFERED_QUESTION_WORDS, PREP_SCHEMA, REPLY_SCHEMA, BadReply, Card,
                             Question, Term, load_json, parse_prep, parse_recap, parse_reply, question, text_lang)
from callcoach.protocol import CARD_KINDS

ALL = ("question", "tip", "steer")


def words(n: int) -> str:
    return " ".join(["word"] * n)


def test_a_card_and_goal_updates_are_read():
    r = parse_reply({"card": {"kind": "question", "text": " מי  מחליט? ", "lang": "he"},
                     "goals": [{"id": "g1", "done": True}]}, ALL)
    assert r.card == Card("question", "מי מחליט?", "he") and r.goals == {"g1": True} and r.problems == ()


def test_no_card_is_the_normal_answer():
    r = parse_reply('{"card": null, "goals": []}', ALL)
    assert r.card is None and r.goals == {}


def test_json_inside_text_or_a_code_fence_is_read():
    text = 'Here:\n```json\n{"card": null, "goals": [{"id": "g2", "done": false}]}\n```'
    assert parse_reply(text, ALL).goals == {"g2": False}


def test_a_kind_that_is_not_allowed_drops_only_the_card():
    r = parse_reply({"card": {"kind": "tip", "text": "x", "lang": "he"}, "goals": [{"id": "g1", "done": True}]},
                    ("question",))
    assert r.card is None and r.goals == {"g1": True} and "not allowed" in r.problems[0]


def test_a_long_question_is_dropped():
    text = words(OFFERED_QUESTION_WORDS + 1) + "?"
    r = parse_reply({"card": {"kind": "question", "text": text, "lang": "en"}, "goals": []}, ALL)
    assert r.card is None and "too long" in r.problems[0]


@pytest.mark.parametrize("kind, limit, forced", [("tip", NUDGE_WORDS, False), ("steer", NUDGE_WORDS, False),
                                                 ("question", OFFERED_QUESTION_WORDS, False),
                                                 ("question", LONG_CARD_WORDS, True)])
def test_each_card_has_a_word_limit(kind, limit, forced):
    def card(n):
        return parse_reply({"card": {"kind": kind, "text": words(n), "lang": "en"}, "goals": []}, ALL,
                           forced=forced).card

    assert card(limit) is not None and card(limit + 1) is None


def test_terms_are_read_and_cut_to_five():
    raw = [{"term": f" T{i} ", "note": " a  note "} for i in range(MAX_TERMS + 1)]
    r = parse_reply({"card": None, "goals": [], "terms": raw}, ALL)
    assert MAX_TERMS == 5
    assert r.terms == tuple(Term(f"T{i}", "a note") for i in range(MAX_TERMS)) and "kept" in r.problems[0]


def test_five_terms_are_all_kept():
    raw = [{"term": t, "note": "הסבר קצר", "kind": "term"} for t in ("SOC 2", "Okta", "SCIM", "SAML", "JIT")]
    r = parse_reply({"card": None, "goals": [], "terms": raw}, ALL)
    assert [t.term for t in r.terms] == ["SOC 2", "Okta", "SCIM", "SAML", "JIT"] and r.problems == ()


def test_bad_or_long_terms_are_skipped():
    raw = [{"term": "SOC 2", "note": "audit standard"}, {"term": "x"}, "y", {"term": "", "note": "n"},
           {"term": "Okta", "note": words(13)}]
    r = parse_reply({"card": None, "goals": [], "terms": raw}, ALL)
    assert r.terms == (Term("SOC 2", "audit standard"),) and len(r.problems) == 4


def test_a_reply_without_terms_has_none():
    assert parse_reply({"card": None, "goals": []}, ALL).terms == ()
    assert parse_reply({"card": None, "goals": [], "terms": "SOC 2"}, ALL).problems == ("terms is not a list",)


def test_bad_goals_are_skipped():
    r = parse_reply({"card": None, "goals": [{"id": "g1"}, "x", {"id": "g2", "done": True}]}, ALL)
    assert r.goals == {"g2": True} and len(r.problems) == 2


@pytest.mark.parametrize("raw", ["", "no json here", "[1, 2]", 5])
def test_garbage_raises_bad_reply(raw):
    with pytest.raises(BadReply):
        parse_reply(raw, ALL)


def test_prep_reply_is_cut_to_its_limits():
    p = parse_prep(json.dumps({"goals": [f"g{i}" for i in range(9)], "questions": ["a", "b", "c", "d"],
                               "glossary": ["SOC 2", " ", "Okta"]}))
    assert len(p.goals) == 5 and p.questions == tuple(map(Question, "abc")) and p.glossary == ("SOC 2", "Okta")


def test_schemas_are_strict_objects():
    for schema in (REPLY_SCHEMA, PREP_SCHEMA):
        assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert "asked" in REPLY_SCHEMA["required"]
    card = REPLY_SCHEMA["properties"]["card"]["anyOf"][1]
    assert card["properties"]["kind"]["enum"] == ["question", "tip", "steer"] and "he" in card["required"]
    for items in (REPLY_SCHEMA["properties"]["questions"]["items"], PREP_SCHEMA["properties"]["questions"]["items"],
                  REPLY_SCHEMA["properties"]["asked"]["anyOf"][1]):
        assert items["required"] == ["text", "he"] and items["additionalProperties"] is False


def test_load_json_passes_dicts_through():
    assert load_json({"a": 1}) == {"a": 1}


@pytest.mark.parametrize("goals", [5, True, "g1", {"id": "g1", "done": True}])
def test_goals_that_are_not_a_list_raise_bad_reply(goals):
    with pytest.raises(BadReply, match="goals"):
        parse_reply({"card": None, "goals": goals}, ALL)


def test_terms_keep_their_kind_and_default_to_term():
    raw = [{"term": "Dana", "note": "IT manager", "kind": "person"}, {"term": "SOC 2", "note": "audit"},
           {"term": "Okta", "note": "login", "kind": "spaceship"}]
    r = parse_reply({"card": None, "goals": [], "terms": raw}, ALL)
    assert [t.kind for t in r.terms] == ["person", "term", "term"]
    assert r.problems == ("term 'Okta' has kind 'spaceship'",)


def test_live_questions_are_read_cut_to_two_and_long_ones_skipped():
    raw = [" מה היה  הכי קשה? ", words(MAX_QUESTION_WORDS + 1), "מי עוד מעורב?", "ומה התקציב?", 7]
    r = parse_reply({"card": None, "goals": [], "terms": [], "questions": raw}, ALL)
    assert r.questions == (Question("מה היה הכי קשה?"), Question("מי עוד מעורב?"))
    assert len(r.problems) == 3


def test_a_reply_without_questions_keeps_none():
    assert parse_reply({"card": None, "goals": []}, ALL).questions == ()
    assert parse_reply({"card": None, "goals": [], "questions": "x"}, ALL).problems == ("questions is not a list",)


def test_a_term_detail_is_one_paragraph_of_at_most_sixty_words():
    from callcoach.cards import MAX_DETAIL_WORDS, parse_detail

    assert parse_detail("  שירות התחברות.\n\nהוא עלה  כי דנה סיפרה. ") == "שירות התחברות. הוא עלה כי דנה סיפרה."
    cut = parse_detail(" ".join(["מילה"] * 70))
    assert len(cut.split()) == MAX_DETAIL_WORDS and cut.endswith("…")
    assert parse_detail(None) == "" and parse_detail({"text": "x"}) == ""


def test_insight_is_no_card_kind_any_more():
    assert "insight" not in CARD_KINDS
    r = parse_reply({"card": {"kind": "insight", "text": "x", "lang": "en"}, "goals": []}, CARD_KINDS)
    assert r.card is None and "not allowed" in r.problems[0]


def test_live_questions_carry_their_hebrew_translation():
    raw = [{"text": "What did it cost?", "he": " כמה  זה עלה? "}, {"text": "מי מאשר?", "he": "מי מאשר?"}]
    r = parse_reply({"card": None, "goals": [], "questions": raw}, ALL)
    assert r.questions == (Question("What did it cost?", "כמה זה עלה?"), Question("מי מאשר?", "")) and r.problems == ()


def test_a_translation_that_is_too_long_is_dropped_and_the_question_kept():
    raw = [{"text": "What did it cost?", "he": " ".join(["מילה"] * (MAX_HE_WORDS + 1))}]
    r = parse_reply({"card": None, "goals": [], "questions": raw}, ALL)
    assert r.questions == (Question("What did it cost?"),) and "translation is too long" in r.problems[0]


def test_a_translation_that_says_the_same_or_is_not_text_is_empty():
    assert question("Okta?", "okta").he == "" and question("Okta?", "מה זה Okta?").he == "מה זה Okta?"
    r = parse_reply({"card": None, "goals": [], "questions": [{"text": "Why now?", "he": 5}]}, ALL)
    assert r.questions == (Question("Why now?"),)


def test_a_question_card_has_its_translation_and_a_tip_never_does():
    q = parse_reply({"card": {"kind": "question", "text": "Who signs off?", "lang": "en", "he": "מי מאשר?"},
                     "goals": []}, ALL).card
    assert q == Card("question", "Who signs off?", "en", "מי מאשר?")
    tip = parse_reply({"card": {"kind": "tip", "text": "תבקש דוגמה", "lang": "he", "he": "something"},
                       "goals": []}, ALL).card
    assert tip == Card("tip", "תבקש דוגמה", "he", "")


def test_the_asked_question_is_read_with_its_translation():
    r = parse_reply({"card": None, "goals": [], "asked": {"text": " How is  yours different? ", "he": "במה שלכם שונה?"}},
                    ALL)
    assert r.asked == Question("How is yours different?", "במה שלכם שונה?")
    assert parse_reply({"card": None, "goals": [], "asked": None}, ALL).asked is None
    assert parse_reply({"card": None, "goals": []}, ALL).asked is None


def test_a_bad_or_long_asked_question_is_dropped():
    long = parse_reply({"card": None, "goals": [], "asked": {"text": words(MAX_ASKED_WORDS + 1), "he": ""}}, ALL)
    assert long.asked is None and "too long" in long.problems[0]
    bad = parse_reply({"card": None, "goals": [], "asked": {"he": "x"}}, ALL)
    assert bad.asked is None and bad.problems


def test_opening_questions_from_prep_carry_their_translation():
    p = parse_prep({"goals": [], "glossary": [], "questions": [{"text": "Why now?", "he": "למה עכשיו?"}, "מה הכאב?"]})
    assert p.questions == (Question("Why now?", "למה עכשיו?"), Question("מה הכאב?"))


def test_the_language_of_a_list_of_questions_is_hebrew_only_when_all_are():
    assert text_lang(["מה?", "מי?"]) == "he" and text_lang(["מה?", "Why?"]) == "en" and text_lang([]) == "he"


def test_a_recap_is_up_to_five_plain_lines():
    text = "- דנה סיפרה על הביקורת\n\n* הם עובדים עם Okta\n1. סיכמנו הצעה\n2) עוד שורה\n# חמישית\nשישית"
    assert parse_recap(text).split("\n") == ["דנה סיפרה על הביקורת", "הם עובדים עם Okta", "סיכמנו הצעה", "עוד שורה",
                                             "חמישית"]
    assert parse_recap(None) == "" and parse_recap("  \n - ") == ""


def test_a_long_recap_is_cut_to_seventy_words():
    from callcoach.cards import MAX_RECAP_WORDS

    text = parse_recap("\n".join([" ".join(["מילה"] * 30)] * 3))
    assert len(text.split()) == MAX_RECAP_WORDS and text.endswith("…")


def test_translations_keep_only_the_lines_asked_for_in_their_order():
    from callcoach.cards import MAX_TRANSLATION_WORDS, parse_translations

    reply = json.dumps({"lines": [{"id": 5, "he": " מי מאשר  אצלכם? "}, {"id": 4, "he": "שבוע שעבר זה נתקע"},
                                  {"id": 9, "he": "לא ביקשו"}, {"id": 4, "he": "שוב"}, {"id": 6, "he": ""},
                                  "x", {"id": "7", "he": "מחרוזת"}]})
    found, problems = parse_translations(reply, [4, 5, 6])
    assert found == ((4, "שבוע שעבר זה נתקע"), (5, "מי מאשר אצלכם?"))
    assert len(problems) == 5
    long, _ = parse_translations({"lines": [{"id": 1, "he": " ".join(["מילה"] * 80)}]}, [1])
    assert len(long[0][1].split()) == MAX_TRANSLATION_WORDS and long[0][1].endswith("…")
    assert parse_translations({"lines": "x"}, [1]) == ((), ("lines is not a list",))
    with pytest.raises(BadReply):
        parse_translations("not json", [1])


def test_the_live_reply_has_the_now_line_and_empty_keeps_the_shown_one():
    assert REPLY_SCHEMA["properties"]["now"] == {"type": "string"} and "now" in REPLY_SCHEMA["required"]
    r = parse_reply({"card": None, "goals": [], "now": "  דנה מסבירה איך  בקשות גישה עוברות היום "}, ALL)
    assert r.now == "דנה מסבירה איך בקשות גישה עוברות היום" and r.problems == ()
    assert parse_reply({"card": None, "goals": [], "now": ""}, ALL).now == ""
    assert parse_reply({"card": None, "goals": []}, ALL).now == ""


def test_a_bad_or_long_now_line_is_dropped():
    assert parse_reply({"card": None, "goals": [], "now": 5}, ALL).problems == ("now is not a string",)
    assert parse_reply({"card": None, "goals": [], "now": words(MAX_NOW_WORDS)}, ALL).now == words(MAX_NOW_WORDS)
    long = parse_reply({"card": None, "goals": [], "now": words(MAX_NOW_WORDS + 1)}, ALL)
    assert long.now == "" and "too long" in long.problems[0]
