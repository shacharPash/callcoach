import json

from callcoach.export import EXPORT_ENV, export_and_mark, export_call, export_root, transcript_text


def call(tmp_path, summary="## סיכום\nהכול טוב\n"):
    folder = tmp_path / "data" / "calls" / "2026-11-23-1000-demo"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"started": 1000.0}), encoding="utf-8")
    rows = [{"kind": "line", "id": 2, "segment_id": 2, "channel": "them", "lang": "en", "text": "Hi there",
             "t_start": 1065.0, "speech_end": 1066.0, "line_ready": 1067.0},
            {"kind": "line", "id": 1, "segment_id": 1, "channel": "me", "lang": "he", "text": "שלום",
             "t_start": 1005.0, "speech_end": 1006.0, "line_ready": 1007.0},
            {"kind": "bookmark", "t": 1010.0}]
    (folder / "lines.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")
    (folder / "me.wav").write_bytes(b"RIFF")
    if summary is not None:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    return folder


def test_the_folder_comes_from_the_environment_only_when_set(tmp_path):
    assert export_root({}) is None and export_root({EXPORT_ENV: "  "}) is None
    assert export_root({EXPORT_ENV: str(tmp_path)}) == tmp_path


def test_an_empty_call_says_it_has_no_lines(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    assert transcript_text(folder) == "# תמלול\n\nאין שורות\n"


def test_a_call_is_copied_without_its_audio(tmp_path):
    folder, root = call(tmp_path), tmp_path / "drive"
    root.mkdir()
    target = export_call(folder, root)
    assert target == root / folder.name
    assert sorted(p.name for p in target.iterdir()) == ["summary.md", "transcript.md"]
    assert (target / "summary.md").read_text(encoding="utf-8") == "## סיכום\nהכול טוב\n"
    assert (target / "transcript.md").read_text(encoding="utf-8") == \
        "# תמלול\n\n[00:05] אני: שלום\n[01:05] הם: Hi there\n"


def test_a_call_with_no_summary_still_gets_its_transcript(tmp_path):
    folder, root = call(tmp_path, summary=None), tmp_path / "drive"
    root.mkdir()
    assert sorted(p.name for p in export_call(folder, root).iterdir()) == ["transcript.md"]


def test_no_folder_means_no_copy_and_no_mark(tmp_path):
    folder = call(tmp_path)
    assert export_call(folder, None) is None and export_and_mark(folder, None) is None
    assert "export_error" not in json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def test_a_missing_folder_is_an_error_kept_in_meta_and_nothing_is_created(tmp_path):
    folder, root = call(tmp_path), tmp_path / "not-mounted"
    error = export_and_mark(folder, root)
    assert error and "not there" in error and not root.exists()
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["export_error"] == error


def test_a_later_copy_replaces_the_files_and_clears_the_mark(tmp_path):
    folder, root = call(tmp_path), tmp_path / "drive"
    assert export_and_mark(folder, root)
    root.mkdir()
    (folder / "summary.md").write_text("## סיכום מלא\n", encoding="utf-8")
    assert export_and_mark(folder, root) is None
    assert (root / folder.name / "summary.md").read_text(encoding="utf-8") == "## סיכום מלא\n"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["export_error"] is None and meta["started"] == 1000.0
    assert not [p for p in (root / folder.name).iterdir() if p.name.endswith(".tmp")]
