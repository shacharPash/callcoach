from callcoach.language import ConversationLanguage, LanguageRouter


def test_short_segments_keep_the_current_language():
    r = LanguageRouter("he")
    assert not r.needs_detection(1.0)
    assert r.choose(1.0, {"en": 0.99}) == ("he", None)


def test_a_confident_detection_switches():
    r = LanguageRouter("he")
    lang, found = r.choose(3.0, {"en": 0.9, "he": 0.05})
    assert (lang, found) == ("en", "en")
    r.commit(found)
    assert r.choose(1.0, None) == ("en", None)


def test_an_unsure_detection_keeps_the_current_language():
    r = LanguageRouter("he")
    assert r.choose(3.0, {"en": 0.5, "he": 0.4}) == ("he", None)


def test_other_languages_are_ignored_in_the_ratio():
    r = LanguageRouter("en")
    assert r.choose(3.0, {"he": 0.3, "ar": 0.6, "en": 0.05}) == ("he", "he")


def test_lock_stops_detection():
    r = LanguageRouter("he")
    r.lock("en")
    assert not r.needs_detection(5.0)
    assert r.choose(5.0, {"he": 1.0}) == ("en", None)


def test_conversation_switches_after_two_lines():
    c = ConversationLanguage("he")
    assert c.observe("en") == "he"
    assert c.observe("en") == "en"


def test_conversation_streak_resets():
    c = ConversationLanguage("he")
    c.observe("en")
    c.observe("he")
    assert c.observe("en") == "he"


def test_a_tiny_hebrew_english_mass_keeps_the_current_language():
    assert LanguageRouter("en").choose(3.0, {"he": 0.001, "en": 0.0003, "ar": 0.99}) == ("en", None)


def test_a_tie_keeps_the_current_language():
    assert LanguageRouter("he").choose(3.0, {"he": 0.4, "en": 0.4}) == ("he", None)


def test_a_missing_language_counts_as_zero():
    assert LanguageRouter("he").choose(3.0, {"en": 0.9}) == ("en", "en")


def test_switching_back_to_hebrew():
    r = LanguageRouter("he")
    r.commit(r.choose(3.0, {"en": 0.9})[1])
    assert r.current == "en"
    lang, found = r.choose(3.0, {"he": 0.9, "en": 0.05})
    assert (lang, found) == ("he", "he")
    r.commit(found)
    assert r.current == "he"


def test_the_duration_boundary_is_inclusive():
    r = LanguageRouter("he")
    assert r.needs_detection(1.5)
    assert r.choose(1.5, {"en": 0.75, "he": 0.25}) == ("en", "en")


def test_detected_reports_only_clear_detections_and_changes_nothing():
    r = LanguageRouter("he")
    assert r.detected(3.0, {"en": 0.9}) == "en"
    assert r.detected(3.0, {"en": 0.5, "he": 0.4}) is None
    assert r.detected(1.0, {"en": 0.99}) is None
    assert r.detected(3.0, None) is None
    assert r.current == "he"


def test_conversation_lock_mid_streak_stops_switching():
    c = ConversationLanguage("he")
    c.observe("en")
    c.lock("he")
    assert c.observe("en") == "he"
    assert c.observe("en") == "he"


def test_choose_does_not_change_the_current_language():
    r = LanguageRouter("he")
    assert r.choose(3.0, {"en": 0.9, "he": 0.05}) == ("en", "en")
    assert r.current == "he"


def test_commit_switches_and_respects_the_lock():
    r = LanguageRouter("he")
    r.commit("en")
    assert r.current == "en"
    r.lock("he")
    r.commit("en")
    assert r.current == "he"


def test_choose_keeps_the_current_language_when_unsure():
    r = LanguageRouter("en")
    assert r.choose(0.8, {"he": 0.99}) == ("en", None)


def test_an_unsure_english_detection_keeps_hebrew():
    r = LanguageRouter("he")
    assert r.choose(9.0, {"en": 0.44, "nn": 0.39}) == ("he", None)
    assert r.choose(8.0, {"en": 0.39, "nn": 0.2}) == ("he", None)


def test_a_clear_english_detection_still_switches():
    assert LanguageRouter("he").choose(3.0, {"en": 0.6, "he": 0.1}) == ("en", "en")


def test_unlock_lets_detection_decide_again():
    r = LanguageRouter("he")
    r.lock("en")
    r.unlock()
    assert r.needs_detection(5.0) and r.current == "en"
    assert r.choose(5.0, {"he": 0.95}) == ("he", "he")
    c = ConversationLanguage("he")
    c.lock("en")
    c.unlock()
    c.observe("he")
    assert c.observe("he") == "he"
