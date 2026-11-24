from callcoach.data import ME, THEM, Line, Segment
from callcoach.textfilter import EchoFilter, clean, similarity


def line(id, channel, text, end, segment_id=None):
    return Line(id, segment_id or id, channel, "he", text, end - 1.0, end, end + 1.0)


def test_clean_drops_noise_and_hallucinations():
    for text in ["", "  ", "...", "[BLANK_AUDIO]", "(music)", "תודה שצפיתם", "Thanks for watching!",
                 "[music] [applause]", "(music) [BLANK_AUDIO]"]:
        assert clean(text) is None
    assert clean("  שלום,   מה שלומך? ") == "שלום, מה שלומך?"


def test_mic_echo_of_the_other_side_is_dropped():
    f = EchoFilter()
    assert f.add(line(1, THEM, "מה שלומכם היום", 10.0), now=11.0) == [line(1, THEM, "מה שלומכם היום", 10.0)]
    assert f.add(line(2, ME, "מה שלומכם היום?", 10.3), now=11.2) == []


def test_clean_keeps_text_around_a_bracketed_word():
    assert clean("(laughs) yes I agree (pause)") == "(laughs) yes I agree (pause)"


def test_different_text_is_kept():
    f = EchoFilter()
    f.add(line(1, THEM, "מה שלומכם היום", 10.0), now=11.0)
    f.advance(11.5)
    mine = line(2, ME, "הכל טוב תודה", 10.5)
    assert f.add(mine, now=11.5) == [mine]


def test_same_text_far_apart_is_kept():
    f = EchoFilter()
    f.add(line(1, THEM, "כן", 10.0), now=11.0)
    f.advance(21.0)
    mine = line(2, ME, "כן", 20.0)
    assert f.add(mine, now=21.0) == [mine]


def test_mic_line_waits_until_the_other_side_is_segmented():
    f = EchoFilter()
    f.advance(10.5)
    mine = line(2, ME, "הכל טוב", 10.2)
    assert f.add(mine, now=10.6) == []
    f.advance(11.0)
    assert f.tick(now=11.0) == [mine]


def test_mic_line_waits_for_an_overlapping_them_segment():
    f = EchoFilter()
    f.advance(11.0)
    f.expect(Segment(7, THEM, b"", 9.0, 10.0, 10.6))
    mine = line(2, ME, "מה שלומכם היום", 10.2)
    assert f.add(mine, now=11.0) == []
    theirs = line(3, THEM, "מה שלומכם היום", 10.0, segment_id=7)
    assert f.add(theirs, now=11.4) == [theirs]
    assert f.tick(now=20.0) == []


def test_held_line_is_released_when_the_them_segment_gives_nothing():
    f = EchoFilter()
    f.advance(11.0)
    f.expect(Segment(7, THEM, b"", 9.0, 10.0, 10.6))
    mine = line(2, ME, "הכל טוב", 10.2)
    assert f.add(mine, now=11.0) == []
    f.done(7)
    assert f.tick(now=11.1) == [mine]


def test_held_line_is_released_after_the_timeout():
    f = EchoFilter(max_hold_s=3.0)
    f.expect(Segment(7, THEM, b"", 9.0, 10.0, 10.6))
    mine = line(2, ME, "הכל טוב", 10.2)
    f.add(mine, now=11.0)
    assert f.tick(now=13.0) == []
    assert f.tick(now=14.1) == [mine]


def test_long_lines_with_a_few_different_words_are_similar():
    a = ("אז בעצם מה שאני רוצה להציע זה שנתחיל מהחלק של ההרשמה ואחרי זה נעבור לתשלומים כי שם יש הכי הרבה "
         "תקלות שהלקוחות מתלוננים עליהן בחודשים האחרונים ואני חושב שאם נסדר את זה קודם נוכל להראות תוצאות "
         "כבר בסוף הרבעון ולהציג את זה להנהלה בפגישה הבאה שלנו ביום חמישי")
    b = a.replace("ההרשמה", "הרשמה").replace("תקלות", "בעיות").replace("הרבעון", "החודש").replace("חמישי", "שלישי")
    assert similarity(a, b) >= 0.8


def test_clean_keeps_real_sentences_that_mention_a_hallucination_phrase():
    assert clean("דיברתי היום עם חברי הכנסת") == "דיברתי היום עם חברי הכנסת"
    assert clean("Thank you for watching.") is None
    assert clean("תודה על הצפייה!") is None


def test_a_line_that_only_repeats_the_glossary_is_dropped():
    assert clean("SOC 2, Okta.", glossary=("SOC 2", "Okta")) is None
    assert clean("We use Okta for SOC 2.", glossary=("SOC 2", "Okta")) == "We use Okta for SOC 2."
    assert clean("Okta", glossary=()) == "Okta"


def test_one_glossary_item_said_alone_is_kept():
    assert clean("Nimbus Labs.", glossary=("Nimbus Labs", "Okta")) == "Nimbus Labs."
    assert clean("Okta Workflows.", glossary=("Okta", "Okta Workflows")) == "Okta Workflows."
    assert clean("Okta, Nimbus Labs.", glossary=("Nimbus Labs", "Okta")) is None
