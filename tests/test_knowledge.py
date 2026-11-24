from callcoach.knowledge import Entry, Knowledge, load_knowledge, local_only, ours_line, ours_link, parse, words

SAMPLE = """# ידע: דוגמה

איך הקובץ עובד:
- כל רשומה מתחילה בכותרת "## ".

# חברות

## Check Point
סוג: חברה
כתיבים: צ'ק פוינט, צק פוינט, CHKP
תקציר: ענקית אבטחת רשת ישראלית ותיקה
פרטים:
- תחום: אבטחת רשת
שלנו:
- שיחה עם ה-CISO על חולשות, דנה, 2026-03-02, מקור: "סיכום" (https://example.com/a)
- כנס משותף, דנה ויוסי, 2026-05-10, מקור: "יומן" (https://example.com/b)
- היכרות דרך חבר בלי תאריך
מקורות: https://example.com/c (נכון ל-2026-11)

## Gem Security
סוג: חברה
כתיבים: ג'ם*, גם*, גם סקיוריטי*, Gem*, Gem Security
תקציר: זיהוי ותגובה לאיומים בענן
שלנו: אין עדיין

## Glow
סוג: חברה
כתיבים: גלואו, Glow*
תקציר: אבטחת מכשירים

# קרנות

## Team8
סוג: קרן
כתיבים: טים 8, טים-8
תקציר: קרן ואולפן חברות
שלנו: פגישה עם שותף, יוסי, 2026-01-20

## Twin One
סוג: חברה
כתיבים: טווין
תקציר: חברה ראשונה

## Twin Two
סוג: חברה
כתיבים: טווין
תקציר: חברה שנייה

## No Summary
סוג: חברה
כתיבים: בלי תקציר

# מונחים

## SASE
סוג: מונח
כתיבים: סאסי, סאשי
תקציר: רשת ואבטחה כשירות ענן
דוגמאות: Cato Networks
"""


def sample() -> Knowledge:
    entries, skipped = parse(SAMPLE)
    return Knowledge(entries, skipped=skipped)


def names(found) -> list[str]:
    return [e.name for e in found]


def test_entries_are_read_with_their_fields():
    entries, skipped = parse(SAMPLE)
    assert [e.name for e in entries] == ["Check Point", "Gem Security", "Glow", "Team8", "Twin One", "Twin Two",
                                         "SASE"]
    assert skipped == 1
    check = entries[0]
    assert check.kind == "company" and check.summary == "ענקית אבטחת רשת ישראלית ותיקה"
    assert entries[3].kind == "fund" and entries[6].kind == "term"
    assert entries[1].ours == () and entries[3].ours == ("פגישה עם שותף, יוסי, 2026-01-20",)


def test_the_details_are_kept_in_the_order_written():
    entries, _ = parse(SAMPLE)
    assert entries[0].details == ("תחום: אבטחת רשת",) and entries[1].details == ()
    found = parse("## Wiz\nתקציר: אבטחת ענן\nפרטים: הוקמה ב-2020\n- מטה: ניו יורק\n-  \nשלנו:\n- שיחה\n")[0][0]
    assert found.details == ("הוקמה ב-2020", "מטה: ניו יורק") and found.ours == ("שיחה",)
    assert parse("## Wiz\nתקציר: אבטחת ענן\nפרטים: אין עדיין\n")[0][0].details == ()


def test_a_file_is_local_only_by_a_line_in_its_header():
    marked = "# ידע\n\nלמודל: לא\n\n## Wiz\nתקציר: אבטחת ענן\n"
    assert local_only(marked) and local_only("  למודל: לא  \n## Wiz\n") and not local_only(SAMPLE)
    assert not local_only("## Wiz\nתקציר: אבטחת ענן\nלמודל: לא\n")
    assert not local_only("למודל: לא, רק חלק\n## Wiz\n") and not local_only("# למודל: לא\n")
    assert [e.local for e in parse(marked)[0]] == [True] and parse(SAMPLE)[0][0].local is False


def test_a_local_only_file_is_still_matched(tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    (folder / "private.md").write_text("למודל: לא\n\n## Wiz\nכתיבים: ויז\nתקציר: אבטחת ענן\n", encoding="utf-8")
    kb = load_knowledge(tmp_path)
    assert kb.files == ("private",) and [(e.name, e.local) for e in kb.find("עברנו לויז")] == [("Wiz", True)]


def test_ours_is_newest_first_and_the_panel_line_has_no_source():
    check = parse(SAMPLE)[0][0]
    assert [ours_line(o) for o in check.ours] == ["מאי 2026: כנס משותף, דנה ויוסי",
                                                  "מרץ 2026: שיחה עם ה-CISO על חולשות, דנה",
                                                  "היכרות דרך חבר בלי תאריך"]
    assert check.ours[0].endswith("(https://example.com/b)")
    assert ours_line("2026-11-02, שיחה, דנה") == "נובמבר 2026: שיחה, דנה"
    assert ours_line("שיחה, 2026-13-02, דנה") == "שיחה, 2026-13-02, דנה"
    assert ours_line("2026-05-10") == "מאי 2026" and ours_line("כנס ב-2026-05-10") == "מאי 2026: כנס"


def test_a_bullet_opens_the_link_of_its_source():
    assert ours_link('כנס, 2026-05-10, מקור: "יומן" (https://example.com/b)') == "https://example.com/b"
    assert ours_link('שיחה, מקור: "סיכום" (http://example.com/a?x=1)') == "http://example.com/a?x=1"
    assert ours_link('שיחה, 2026-01-05, מקור: "סיכום"') == ""
    assert ours_link("שיחה בלי מקור (https://example.com/c)") == ""
    assert ours_link('שיחה, מקור: "קובץ" (file:///etc/hosts)') == ""
    assert ours_link('שיחה, מקור: "קובץ" (javascript:alert(1))') == ""


def test_every_entry_knows_the_file_it_came_from(tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    (folder / "cyber.md").write_text("## Wiz\nסוג: חברה\nתקציר: אבטחת ענן\n", encoding="utf-8")
    (folder / "drive.md").write_text("## Okta\nסוג: מוצר\nתקציר: התחברות לארגונים\n", encoding="utf-8")
    kb = load_knowledge(tmp_path)
    assert sorted((e.name, e.file) for e in kb.entries) == [("Okta", "drive"), ("Wiz", "cyber")]
    assert parse("## Wiz\nסוג: חברה\nתקציר: אבטחת ענן\n")[0][0].file == ""


def test_a_date_in_the_source_does_not_order_the_bullets():
    entries, _ = parse("## Wiz\nסוג: חברה\nתקציר: אבטחת ענן\nשלנו:\n- שיחה, 2026-01-05, מקור: \"א\"\n"
                       "- היכרות, מקור: \"סיכום 2026-09-01\"\n")
    assert [ours_line(o) for o in entries[0].ours] == ["ינואר 2026: שיחה", "היכרות"]


def test_words_fold_case_marks_and_dashes():
    assert words("צ׳ק-פוינט") == ("צק", "פוינט")
    assert words("Sun.Security, CHKP!") == ("sun", "security", "chkp")
    assert words("וִיז") == ("ויז",)


def test_names_and_spellings_are_found_in_a_line():
    kb = sample()
    assert names(kb.find("דיברנו עם צ'ק פוינט ועם CHKP")) == ["Check Point"]
    assert names(kb.find("they bought check point last year")) == ["Check Point"]
    assert names(kb.find("הם גם השקיעו בטים 8 וגם ב-Team8")) == ["Team8"]
    assert names(kb.find("מה זה סאסי בעצם")) == ["SASE"]


def test_hebrew_prefix_letters_are_allowed_on_the_first_word():
    kb = sample()
    assert names(kb.find("ולצק פוינט אין את זה")) == ["Check Point"]
    assert names(kb.find("שבסאסי")) == ["SASE"]
    assert names(kb.find("אבגדהסאסי")) == []


def test_a_spelling_marked_as_an_everyday_word_is_not_matched_alone():
    kb = sample()
    assert kb.find("גם אנחנו, ג'ם, Gem, glow") == []
    assert names(kb.find("עבדנו עם Gem Security וגלואו")) == ["Gem Security", "Glow"]


def test_a_term_the_agent_names_maps_to_its_entry_even_when_marked():
    kb = sample()
    assert kb.lookup("Gem").name == "Gem Security"
    assert kb.lookup("Glow").name == "Glow"
    assert kb.lookup("צ׳ק פוינט").name == "Check Point"
    assert kb.lookup("Okta") is None and kb.lookup("") is None


def test_a_spelling_of_two_entries_is_dropped_from_both():
    kb = sample()
    assert kb.find("טווין") == [] and kb.lookup("טווין") is None
    assert names(kb.find("Twin One ו-Twin Two")) == ["Twin One", "Twin Two"]
    assert kb.ambiguous == ("טווין",)


def test_a_name_written_with_other_spacing_still_finds_its_entry():
    kb = sample()
    assert kb.lookup("Team 8").name == "Team8" and kb.lookup("CheckPoint").name == "Check Point"
    assert kb.lookup("Gem-Security").name == "Gem Security"


def test_an_entry_name_wins_over_a_spelling_of_another_entry():
    entries, _ = parse("## Cato\nסוג: חברה\nכתיבים: קאטו\nתקציר: חברה קטנה\n\n"
                       "## Cato Networks\nסוג: חברה\nכתיבים: Cato, קאטו נטוורקס\nתקציר: SASE\n")
    kb = Knowledge(entries)
    assert names(kb.find("we use Cato and קאטו נטוורקס")) == ["Cato", "Cato Networks"]
    assert kb.lookup("Cato").name == "Cato" and kb.ambiguous == ()


def test_a_one_word_person_name_is_not_found_inside_another_name():
    entries, _ = parse("## Ron Levi\nסוג: אדם\nכתיבים: רון, רון לוי\nתקציר: יזם\n")
    kb = Knowledge(entries)
    assert kb.find("שרון אמרה, ומרון הגיע") == []
    assert names(kb.find("רון בא")) == ["Ron Levi"] and names(kb.find("ולרון לוי יש")) == ["Ron Levi"]


def test_a_product_kind_is_read():
    entries, _ = parse("## Okta\nסוג: מוצר\nתקציר: התחברות לארגונים\n")
    assert entries[0].kind == "product"


HEBREW_NAMED = ("## מנהל אבטחת מידע\nסוג: מונח\nכתיבים: CISO, Chief Information Security Officer, סיסו\n"
                "תקציר: אחראי האבטחה\n")


def test_a_line_tells_the_spelling_each_entry_was_said_by():
    kb = Knowledge(parse(HEBREW_NAMED)[0])
    [(entry, said)] = kb.said_in("we met the chief information security officer today")
    assert entry.name == "מנהל אבטחת מידע" and said == "Chief Information Security Officer"
    assert [s for _, s in kb.said_in("ולסיסו שלהם")] == ["סיסו"]
    assert names(kb.find("the CISO")) == ["מנהל אבטחת מידע"]


def test_an_entry_with_a_hebrew_name_said_in_english_shows_as_said():
    entry = Entry("מנהל אבטחת מידע", "term", "אחראי האבטחה", ("CISO",))
    assert entry.shown("CISO") == "CISO"
    assert entry.shown("סיסו") == "מנהל אבטחת מידע" and entry.shown("") == "מנהל אבטחת מידע"
    assert Entry("Cato Networks", "company", "SASE", ("Kato Networks",)).shown("Kato Networks") == "Cato Networks"


def test_each_entry_is_found_once_per_line_in_order():
    kb = sample()
    assert names(kb.find("סאסי, צק פוינט, סאשי, CHKP")) == ["SASE", "Check Point"]


def test_the_folder_is_read_without_backups(tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    (folder / "cyber.md").write_text(SAMPLE, encoding="utf-8")
    (folder / "cyber.backup-2026-11-16.md").write_text(SAMPLE.replace("Check Point", "Old Point"), encoding="utf-8")
    (folder / "prompt.md").write_text("You are updating my file.\n", encoding="utf-8")
    (folder / "more.md").write_text("## Check Point\nסוג: חברה\nתקציר: כפול\n\n## Wiz\nסוג: חברה\n"
                                    "כתיבים: וויז\nתקציר: אבטחת ענן\n", encoding="utf-8")
    kb = load_knowledge(tmp_path)
    assert kb.files == ("cyber", "more")
    assert len(kb.entries) == 8 and kb.lookup("check point").summary == "ענקית אבטחת רשת ישראלית ותיקה"
    assert names(kb.find("בוויז")) == ["Wiz"] and kb.lookup("Old Point") is None
    assert kb.describe() == "cyber, more: 8 רשומות"


def test_a_bom_keeps_the_first_entry_and_an_unreadable_file_is_named(tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    (folder / "bom.md").write_bytes("## Wiz\nסוג: חברה\nתקציר: אבטחת ענן\n".encode("utf-8-sig"))
    (folder / "old.md").write_bytes("## Armis\nסוג: חברה\nתקציר: נכסים\n".encode("cp1255"))
    kb = load_knowledge(tmp_path)
    assert [e.name for e in kb.entries] == ["Wiz"] and kb.failed == ("old",)
    assert kb.describe() == "bom: 1 רשומות, לא נקרא: old"


def test_no_folder_means_no_knowledge(tmp_path):
    kb = load_knowledge(tmp_path)
    assert kb.entries == () and kb.files == () and kb.find("צק פוינט") == [] and kb.describe() == ""
