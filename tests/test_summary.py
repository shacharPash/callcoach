import json
import re

from callcoach.context import Goal, Prep
from callcoach.prompts import LECTURE_RULES, SUMMARY_RULES
from callcoach.providers import FakeProvider, ProviderUnavailable
from callcoach.summary import build_input, local_summary, summarize, summary_kind, usage_section

HEBREW = re.compile(r"[֐-׿]")


def write_call(folder, started=1000.0):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"started": started}), encoding="utf-8")
    rows = [
        {"kind": "line", "id": 2, "segment_id": 2, "channel": "them", "lang": "he", "text": "זה לוקח לנו יומיים",
         "t_start": started + 30, "speech_end": started + 32, "line_ready": started + 33},
        {"kind": "line", "id": 1, "segment_id": 1, "channel": "me", "lang": "he", "text": "כמה זמן זה לוקח?",
         "t_start": started + 25, "speech_end": started + 27, "line_ready": started + 28},
        {"kind": "line", "id": 3, "segment_id": 3, "channel": "them", "lang": "en", "text": "We use Okta",
         "t_start": started + 200, "speech_end": started + 202, "line_ready": started + 203},
        {"kind": "bookmark", "t": started + 40},
    ]
    (folder / "lines.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                        encoding="utf-8")


PREP = Prep(call_type="validation", text="דנה מ-Acme", name="Dana")
GOALS = [Goal("g1", "להבין כמה זמן זה לוקח", True), Goal("g2", "מי מחליט")]


def test_the_input_holds_the_transcript_goals_marks_and_notes(tmp_path):
    write_call(tmp_path)
    text = build_input(tmp_path, PREP, GOALS)
    assert text.index("[00:25] me (he): כמה זמן זה לוקח?") < text.index("[00:30] them (he): זה לוקח לנו יומיים")
    assert "- answered: להבין כמה זמן זה לוקח" in text and "- open: מי מחליט" in text
    assert "[g1]" not in text and "# Glossary" in text
    mark = text[text.index("# Marked moments"):text.index("# Transcript")]
    assert "[00:40]" in mark and "זה לוקח לנו יומיים" in mark and "We use Okta" not in mark
    assert "דנה מ-Acme" in text


def test_the_local_summary_is_right_to_left_on_every_line(tmp_path):
    write_call(tmp_path)
    text = local_summary(tmp_path, GOALS, "no agent")
    for row in filter(None, map(str.strip, text.splitlines())):
        first = row.lstrip("#- ").strip()
        assert HEBREW.match(first[0]), row
    assert "להבין כמה זמן זה לוקח" in text and "00:40" in text


def test_a_summary_from_the_strong_model(tmp_path):
    write_call(tmp_path)
    fake = FakeProvider(summary="## מה למדתי\n- זה לוקח יומיים\n")
    used = []
    result = summarize(fake, tmp_path, PREP, GOALS, on_usage=lambda m, u: used.append(m),
                       usage_text=lambda: "בקשות: 3")
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert result.ok and result.path == tmp_path / "summary.md" and "## מה למדתי" in text and result.kind == "call"
    assert "## שימוש" in text and "בקשות: 3" in text and used == ["fake"]
    assert fake.calls[0][0] == "summarize" and "# Transcript" in fake.calls[0][1]
    assert "מונחים" not in text


def test_the_terms_of_the_call_are_listed_before_the_usage(tmp_path):
    write_call(tmp_path)
    terms = [{"id": 1, "term": "SOC 2", "note": "תקן ביקורת אבטחה", "lang": "he"},
             {"id": 2, "term": "Okta", "note": "התחברות לארגונים", "lang": "he"}]
    summarize(FakeProvider(), tmp_path, PREP, GOALS, on_usage=lambda m, u: None,
              usage_text=lambda: "בקשות: 3", terms=terms)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## מושגים שעלו\n- SOC 2: תקן ביקורת אבטחה\n- Okta: התחברות לארגונים\n\n## שימוש" in text


def test_a_known_term_lists_what_we_know_with_its_source(tmp_path):
    write_call(tmp_path)
    terms = [{"id": 1, "term": "Check Point", "note": "אבטחת רשת", "lang": "he", "known": True,
              "ours": ["כנס משותף, יוסי, 2026-05-10, מקור: \"יומן\" (https://example.com/b)"]},
             {"id": 2, "term": "Okta", "note": "התחברות לארגונים", "lang": "he", "known": False, "ours": []}]
    summarize(FakeProvider(), tmp_path, PREP, GOALS, on_usage=lambda m, u: None, terms=terms)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert ("## מושגים שעלו\n- Check Point: אבטחת רשת\n  - שלנו: כנס משותף, יוסי, 2026-05-10, מקור: \"יומן\" "
            "(https://example.com/b)\n- Okta: התחברות לארגונים\n\n") in text


def test_a_failed_request_writes_the_local_summary(tmp_path):
    write_call(tmp_path)
    fake = FakeProvider(errors=[ProviderUnavailable("busy")])
    result = summarize(fake, tmp_path, PREP, GOALS, on_usage=lambda m, u: None, usage_text=lambda: "")
    assert not result.ok and "busy" in result.message
    assert "סיכום מקומי" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_an_empty_reply_writes_the_local_summary(tmp_path):
    write_call(tmp_path)
    result = summarize(FakeProvider(summary=""), tmp_path, PREP, GOALS, on_usage=lambda m, u: None,
                       usage_text=lambda: "")
    assert not result.ok and "empty" in result.message


def test_the_usage_section_is_right_to_left():
    text = usage_section({"input": 1800, "output": 420, "cache_read": 52000, "cache_write": 6100, "requests": 9,
                          "usd": 0.0014})
    assert "בקשות: 9" in text and "1,800" in text and "$0.0014" in text
    assert all(HEBREW.match(row[0]) for row in text.splitlines())


def test_no_agent_writes_the_local_summary(tmp_path):
    write_call(tmp_path)
    result = summarize(None, tmp_path, PREP, GOALS, on_usage=lambda m, u: None, usage_text=lambda: "")
    assert not result.ok and result.message == "no agent"
    assert (tmp_path / "summary.md").exists()


def test_the_input_says_how_to_address_me(tmp_path):
    write_call(tmp_path)
    assert "address me as: masculine" in build_input(tmp_path, PREP, GOALS, address="masculine")
    assert "address me as: not given" in build_input(tmp_path, PREP, GOALS)


def test_summarize_passes_the_address(tmp_path):
    write_call(tmp_path)
    fake = FakeProvider()
    summarize(fake, tmp_path, PREP, GOALS, on_usage=lambda m, u: None, address="feminine")
    assert "address me as: feminine" in fake.calls[0][1]


def test_the_summary_rules_keep_every_line_right_to_left_and_set_the_address():
    for rules in (SUMMARY_RULES, LECTURE_RULES):
        assert "starts with a Hebrew word" in rules and "never first" in rules and "address me as" in rules


def test_terms_json_has_the_detail_of_an_explained_term(tmp_path):
    from callcoach.summary import write_terms

    okta = {"term": "Okta", "note": "שירות התחברות", "kind": "product", "lang": "he", "known": False, "ours": []}
    write_terms(tmp_path, [{**okta, "detail": "שירות התחברות לארגונים."}, {**okta, "term": "SOC 2"}])
    rows = json.loads((tmp_path / "terms.json").read_text(encoding="utf-8"))
    assert rows[0]["detail"] == "שירות התחברות לארגונים." and "detail" not in rows[1]
    assert "ours" not in rows[0]


def line_row(i, channel, text, t):
    return {"kind": "line", "id": i, "segment_id": i, "channel": channel, "lang": "en", "text": text,
            "t_start": t, "speech_end": t + 2, "line_ready": t + 3}


def write_rows(folder, rows, started=1000.0):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"started": started}), encoding="utf-8")
    (folder / "lines.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")


TALK = "the key idea of this talk is that queues need backpressure or they grow without end"


def test_the_summary_kind_follows_my_share_of_the_words():
    them = line_row(1, "them", TALK, 1010)
    assert summary_kind([]) == "lecture"
    assert summary_kind([them]) == "lecture"
    assert summary_kind([them, line_row(2, "me", "right", 1020)]) == "lecture"
    assert summary_kind([them, line_row(2, "me", "so what happens when it is full", 1020)]) == "call"


def test_a_talk_where_i_barely_spoke_gets_lecture_notes(tmp_path):
    write_rows(tmp_path, [line_row(1, "them", TALK, 1010), line_row(2, "me", "right", 1020)])
    fake = FakeProvider(summary="## נקודות מרכזיות\n- תורים צריכים לחץ חוזר\n")
    result = summarize(fake, tmp_path, PREP, GOALS, on_usage=lambda m, u: None)
    assert result.ok and result.kind == "lecture"
    [(kind, message)] = fake.calls
    assert kind == "summarize" and TALK in message


class Rules(FakeProvider):
    def summarize(self, system, message):
        self.rules = system
        return super().summarize(system, message)


def test_each_kind_uses_its_own_rules(tmp_path):
    write_rows(tmp_path / "lecture", [line_row(1, "them", TALK, 1010)])
    write_call(tmp_path / "call")
    lecture, call = Rules(), Rules()
    summarize(lecture, tmp_path / "lecture", PREP, GOALS, on_usage=lambda m, u: None)
    summarize(call, tmp_path / "call", PREP, GOALS, on_usage=lambda m, u: None)
    assert lecture.rules == LECTURE_RULES and call.rules == SUMMARY_RULES


def test_the_local_summary_also_says_its_kind(tmp_path):
    write_rows(tmp_path, [line_row(1, "them", TALK, 1010)])
    assert summarize(None, tmp_path, PREP, GOALS, on_usage=lambda m, u: None).kind == "lecture"


def test_the_input_lists_the_times_callcoach_did_not_hear_me(tmp_path):
    rows = [line_row(1, "them", TALK, 1010), {"kind": "hearing", "on": False, "t": 1065},
            {"kind": "hearing", "on": True, "t": 1130}, {"kind": "hearing", "on": False, "t": 1200}]
    write_rows(tmp_path, rows)
    text = build_input(tmp_path, PREP, GOALS)
    section = text[text.index("# When CallCoach did not hear me"):text.index("# Marked moments")]
    assert "- [01:05] to [02:10]" in section and "- [03:20] to the end" in section


def test_without_a_change_of_hearing_the_input_has_no_such_section(tmp_path):
    write_call(tmp_path)
    assert "did not hear me" not in build_input(tmp_path, PREP, GOALS)


def test_the_summary_rules_never_call_me_silent():
    assert "Never write that I was silent" in SUMMARY_RULES
