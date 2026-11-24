import json

from callcoach.data import ME, THEM, Line
from callcoach.store import TranscriptStore, render, unheard


def test_lines_and_bookmarks_render_in_time_order(tmp_path):
    s = TranscriptStore(tmp_path, started=1000.0)
    s.add(Line(2, 2, THEM, "he", "מה שלומכם", 1012.0, 1013.0, 1014.0))
    s.add(Line(1, 1, ME, "he", "שלום לכולם", 1005.0, 1006.0, 1007.0))
    s.add(Line(3, 3, THEM, "en", "hello", 1065.0, 1066.0, 1067.0))
    s.bookmark(1013.5)
    text = s.render().read_text(encoding="utf-8")
    body = [l for l in text.splitlines() if l.startswith("[")]
    assert body == ["[00:05] אני: שלום לכולם", "[00:12] הצד השני: מה שלומכם", "[01:05] Them: hello", "[00:13]"]
    assert "## Marked moments" in text


def test_render_works_from_the_files_alone(tmp_path):
    s = TranscriptStore(tmp_path, started=1000.0)
    s.add(Line(1, 1, ME, "en", "hi", 1001.0, 1002.0, 1003.0))
    (tmp_path / "transcript.md").unlink(missing_ok=True)
    assert "Me: hi" in render(tmp_path).read_text(encoding="utf-8")


def test_lines_file_is_json_lines(tmp_path):
    s = TranscriptStore(tmp_path, started=0.0)
    s.add(Line(1, 1, ME, "he", "שלום", 1.0, 2.0, 3.0))
    entry = json.loads((tmp_path / "lines.jsonl").read_text(encoding="utf-8"))
    assert entry["kind"] == "line" and entry["text"] == "שלום"


def test_render_skips_a_line_cut_off_by_a_crash(tmp_path):
    s = TranscriptStore(tmp_path, started=0.0)
    s.add(Line(1, 1, ME, "en", "hi", 1.0, 2.0, 3.0))
    with open(tmp_path / "lines.jsonl", "ab") as f:
        f.write('{"kind": "line", "text": "של'.encode("utf-8")[:-1])
    assert "Me: hi" in render(tmp_path).read_text(encoding="utf-8")


def test_render_works_when_meta_is_lost(tmp_path):
    s = TranscriptStore(tmp_path, started=1000.0)
    s.add(Line(1, 1, ME, "en", "hi", 1005.0, 1006.0, 1007.0))
    (tmp_path / "meta.json").write_text("", encoding="utf-8")
    assert "[00:00] Me: hi" in render(tmp_path).read_text(encoding="utf-8")
    (tmp_path / "meta.json").unlink()
    assert "[00:00] Me: hi" in render(tmp_path).read_text(encoding="utf-8")


def test_text_with_a_unicode_line_separator_is_kept(tmp_path):
    s = TranscriptStore(tmp_path, started=0.0)
    s.add(Line(1, 1, ME, "en", "a\u2028b", 1.0, 2.0, 3.0))
    assert "Me: a\u2028b" in render(tmp_path).read_text(encoding="utf-8")


def test_meta_json_gets_fields_and_keeps_the_rest(tmp_path):
    from callcoach.store import update_meta

    TranscriptStore(tmp_path, started=1000.0)
    update_meta(tmp_path, title="שיחה עם Acme", brief="2026-11-17-acme.md")
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"started": 1000.0, "title": "שיחה עם Acme", "brief": "2026-11-17-acme.md"}
    assert not (tmp_path / "meta.json.tmp").exists()


def test_a_name_becomes_a_file_name_part():
    from callcoach.store import slug

    assert slug("שיחת היכרות עם Acme!", "brief") == "שיחת-היכרות-עם-Acme"
    assert slug("../..", "brief") == "brief" and slug("", "call") == "call"
    assert len(slug("x" * 80, "call")) == 40


def test_the_hearing_notes_render_among_the_lines(tmp_path):
    s = TranscriptStore(tmp_path, started=1000.0)
    s.add(Line(1, 1, ME, "he", "שלום לכולם", 1005.0, 1006.0, 1007.0))
    s.hearing(False, 1010.0)
    s.add(Line(2, 2, THEM, "he", "מה שלומכם", 1012.0, 1013.0, 1014.0))
    s.hearing(True, 1070.0)
    body = [l for l in s.render().read_text(encoding="utf-8").splitlines() if l.startswith("[")]
    assert body == ["[00:05] אני: שלום לכולם", "[00:10] מכאן CallCoach לא שומע אותי", "[00:12] הצד השני: מה שלומכם",
                    "[01:10] מכאן CallCoach שומע אותי שוב"]
    rows = [json.loads(l) for l in (tmp_path / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[1] == {"kind": "hearing", "on": False, "t": 1010.0}


def test_the_times_callcoach_did_not_hear_me():
    rows = [{"kind": "line", "t_start": 5.0}, {"kind": "hearing", "on": False, "t": 10.0},
            {"kind": "hearing", "on": True, "t": 20.0}, {"kind": "hearing", "on": False, "t": 30.0}]
    assert unheard(rows) == [(10.0, 20.0), (30.0, None)]
    assert unheard(rows[:3]) == [(10.0, 20.0)] and unheard([]) == []
