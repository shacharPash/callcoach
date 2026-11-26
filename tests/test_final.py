import json

import pytest

from callcoach import final
from callcoach.config import RATE, SAMPLE_BYTES
from callcoach.final import echo_free, final_pass, join, pieces, runs

STARTED = 1000.0


def note(kind, **fields):
    return json.dumps({"type": "note", "t": 1.0, "kind": kind, **fields})


def line(n, channel, lang, text, start, end):
    return {"kind": "line", "id": n, "segment_id": n, "channel": channel, "lang": lang, "text": text,
            "t_start": STARTED + start, "speech_end": STARTED + end, "line_ready": STARTED + end + 1}


def make_call(tmp_path, entries, aec=("on",), meta=None, terms=None):
    folder = tmp_path / "data" / "calls" / "2026-11-25_1430_call"
    folder.mkdir(parents=True)
    (folder / "call.m4a").write_bytes(b"audio")
    (folder / "meta.json").write_text(json.dumps({"started": STARTED, **(meta or {})}))
    (folder / "events.log").write_text("\n".join([note("mic", source="helper")] + [note("aec", state=s) for s in aec]) + "\n")
    (folder / "lines.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    if terms is not None:
        (folder / "terms.json").write_text(json.dumps([{"term": t} for t in terms]))
    return folder


class Client:
    def __init__(self, servers, lang):
        self.servers, self.lang = servers, lang

    def segments(self, pcm, lang, prompt=""):
        self.servers.asked.append((self.lang, len(pcm) / SAMPLE_BYTES / RATE, prompt))
        return self.servers.answers.pop(0)


class Servers:
    def __init__(self, *answers, fail=None):
        self.answers = list(answers)
        self.asked, self.stops, self.fail = [], 0, fail

    def client(self, lang, timeout):
        if self.fail:
            raise self.fail
        return Client(self, lang)

    def stop(self):
        self.stops += 1


def silence(seconds):
    return lambda audio, index: bytes(int(seconds * RATE) * SAMPLE_BYTES)


def lines_of(folder):
    return [json.loads(l) for l in (folder / "lines.jsonl").read_text().splitlines()]


def test_each_side_keeps_the_language_of_its_live_lines_changing_halfway_between_them():
    lines = [line(1, "me", "he", "שלום", 1, 3), line(2, "me", "en", "hello", 9, 11), line(3, "me", "en", "ok", 20, 21)]
    assert runs(lines, STARTED, 60.0, "he") == [(0.0, 6.0, "he"), (6.0, 60.0, "en")]
    assert runs([], STARTED, 60.0, "en") == [(0.0, 60.0, "en")]


def test_a_long_stretch_is_cut_at_the_widest_silence_near_its_end():
    spans = [(0, 100), (101, 200), (205, 280), (281, 400), (402, 700)]
    assert pieces([(0.0, 700.0, "he")], spans, longest=300) == [(0.0, 202.5, "he"), (202.5, 401.0, "he"), (401.0, 700.0, "he")]
    assert pieces([(0.0, 500.0, "en")], [], longest=300) == [(0.0, 300.0, "en"), (300.0, 500.0, "en")]


def test_lines_of_one_speaker_that_follow_each_other_are_one_line():
    got = join([(5.0, 7.0, "them", "he", "כן"), (0.0, 2.0, "me", "he", "שלום"), (2.5, 4.0, "me", "he", "מה נשמע"),
                (7.5, 9.0, "them", "en", "sure"), (30.0, 31.0, "them", "en", "bye")])
    assert got == [(0.0, 4.0, "me", "he", "שלום מה נשמע"), (5.0, 7.0, "them", "he", "כן"),
                   (7.5, 9.0, "them", "en", "sure"), (30.0, 31.0, "them", "en", "bye")]


@pytest.mark.parametrize("states, free", [(("on",), True), (("on", "headphones", "on"), True),
                                          (("on", "stopped"), False), (("on", "headphones", "stopped"), False),
                                          (("off",), False), (("unavailable",), False), ((), False)])
def test_only_a_call_whose_echo_canceller_ran_to_the_end_is_free_of_echo(tmp_path, states, free):
    assert echo_free(make_call(tmp_path, [], aec=states)) is free


def test_the_final_lines_replace_the_live_ones_and_keep_the_marks(tmp_path):
    live = [line(1, "me", "he", "שלום מה", 1, 2), {"kind": "bookmark", "t": STARTED + 3},
            line(2, "me", "he", "נשמע", 2.2, 3), line(3, "them", "he", "הכל טוב", 4, 6),
            {"kind": "hearing", "on": False, "t": STARTED + 7}]
    folder = make_call(tmp_path, live, meta={"glossary": ["Okta"]}, terms=["Acme", "Okta"])
    servers = Servers([(1.0, 3.0, "שלום מה נשמע"), (4.0, 6.0, "הכל טוב ואצלך")], [(4.0, 6.0, "הכל טוב ואצלך")])
    result = final_pass(folder, folder.parents[1], servers=servers, read_channel=silence(10), busy=lambda: False)
    assert result.ok and result.lines == 2
    rows = lines_of(folder)
    assert [r["kind"] for r in rows] == ["bookmark", "hearing", "line", "line"]
    got = [(r["channel"], r["text"], r["t_start"], r["speech_end"]) for r in rows if r["kind"] == "line"]
    assert got == [("me", "שלום מה נשמע", 1001.0, 1003.0), ("them", "הכל טוב ואצלך", 1004.0, 1006.0)]
    assert [json.loads(l) for l in (folder / "lines.live.jsonl").read_text().splitlines()] == live
    assert [(lang, secs) for lang, secs, _ in servers.asked] == [("he", 10.0), ("he", 10.0)]
    assert {prompt for *_, prompt in servers.asked} == {"Okta, Acme"} and servers.stops >= 1
    assert json.loads((folder / "meta.json").read_text())["final_transcript"]["lines"] == 2


def test_my_line_that_only_repeats_the_other_side_is_dropped_and_a_short_answer_is_kept(tmp_path):
    folder = make_call(tmp_path, [line(1, "me", "he", "כן", 1, 2), line(2, "them", "he", "נשלח את זה ביום ראשון", 1, 3)])
    servers = Servers([(1.2, 3.1, "נשלח את זה ביום ראשון"), (8.0, 8.5, "כן")], [(1.0, 3.0, "נשלח את זה ביום ראשון")])
    assert final_pass(folder, folder.parents[1], servers=servers, read_channel=silence(10), busy=lambda: False).ok
    assert [(r["channel"], r["text"]) for r in lines_of(folder)] == [("them", "נשלח את זה ביום ראשון"), ("me", "כן")]


def test_a_run_again_starts_from_the_live_lines(tmp_path):
    folder = make_call(tmp_path, [line(1, "them", "en", "hello there", 1, 2)])
    for text in ("hello there friend", "hello there again"):
        servers = Servers([], [(1.0, 2.0, text)])
        assert final_pass(folder, folder.parents[1], servers=servers, read_channel=silence(5), busy=lambda: False).ok
    assert [r["text"] for r in lines_of(folder)] == ["hello there again"]
    assert [json.loads(l)["text"] for l in (folder / "lines.live.jsonl").read_text().splitlines()] == ["hello there"]


def test_it_waits_while_another_call_records(tmp_path):
    folder = make_call(tmp_path, [line(1, "them", "en", "hello", 1, 2)])
    waits, busy = [], iter([True, True, False, False])
    servers = Servers([], [(1.0, 2.0, "hello")])
    assert final_pass(folder, folder.parents[1], servers=servers, read_channel=silence(5), busy=lambda: next(busy),
                      sleep=waits.append).ok
    assert waits == [final.WAIT_S, final.WAIT_S] and servers.stops >= 2


def test_a_failure_leaves_the_call_as_it_was(tmp_path):
    live = [line(1, "them", "en", "hello", 1, 2)]
    folder = make_call(tmp_path, live)
    before = (folder / "lines.jsonl").read_text()
    result = final_pass(folder, folder.parents[1], servers=Servers(fail=RuntimeError("no server")),
                        read_channel=silence(5), busy=lambda: False)
    assert not result.ok and "no server" in result.reason
    assert (folder / "lines.jsonl").read_text() == before and not (folder / "lines.live.jsonl").exists()


def test_a_call_with_echo_in_the_mic_or_no_speech_found_keeps_its_live_lines(tmp_path):
    folder = make_call(tmp_path, [line(1, "them", "en", "hello", 1, 2)], aec=("off",))
    before = (folder / "lines.jsonl").read_text()
    assert not final_pass(folder, folder.parents[1], servers=Servers(), read_channel=silence(5), busy=lambda: False).ok
    other = make_call(tmp_path / "b", [line(1, "them", "en", "hello", 1, 2)])
    assert not final_pass(other, other.parents[1], servers=Servers([], []), read_channel=silence(5),
                          busy=lambda: False).ok
    assert (folder / "lines.jsonl").read_text() == before == (other / "lines.jsonl").read_text()
