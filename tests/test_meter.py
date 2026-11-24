from callcoach.meter import TIP, Meter


def talk(m, start, me, them, rounds):
    t = start
    for _ in range(rounds):
        m.add("me", me, t)
        t += me
        m.add("them", them, t)
        t += them
    return t


def test_no_tip_in_the_first_two_minutes():
    m = Meter()
    end = talk(m, 0.0, 9.0, 1.0, 10)
    assert m.tip(end, started=0.0) is None


def test_a_tip_when_i_talk_most_of_the_last_three_minutes():
    m = Meter()
    end = talk(m, 0.0, 9.0, 1.0, 20)
    assert m.tip(end, started=0.0) == TIP


def test_the_tip_is_always_hebrew():
    assert TIP.startswith("חלק")


def test_the_tip_waits_five_minutes_before_it_repeats():
    m = Meter()
    end = talk(m, 0.0, 9.0, 1.0, 20)
    assert m.tip(end, started=0.0) == TIP
    end = talk(m, end, 9.0, 1.0, 10)
    assert m.tip(end, started=0.0) is None
    end = talk(m, end, 9.0, 1.0, 25)
    assert m.tip(end, started=0.0) == TIP


def test_a_balanced_call_gets_no_tip():
    m = Meter()
    end = talk(m, 0.0, 5.0, 5.0, 30)
    assert m.tip(end, started=0.0) is None


def test_old_talk_leaves_the_window():
    m = Meter()
    end = talk(m, 0.0, 9.0, 1.0, 20)
    end = talk(m, end, 1.0, 9.0, 20)
    assert m.share(end) < 0.5
