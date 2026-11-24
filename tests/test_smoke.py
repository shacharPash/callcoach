import importlib
import os

import pytest

import callcoach
import callcoach.config as config


def test_version():
    assert callcoach.__version__ == "0.2.0"


from callcoach.config import BYTES_PER_SECOND, MODEL_FILES, model_path
from callcoach.data import Segment


def test_segment_duration():
    seg = Segment(1, "me", bytes(BYTES_PER_SECOND * 2), 0.0, 1.5, 2.1)
    assert seg.duration == 2.0


def test_model_paths_cover_every_model():
    assert set(MODEL_FILES) == {"he", "en", "langid", "vad"}
    assert model_path("langid").name == "ggml-base.bin"


def test_an_empty_env_var_uses_the_default(monkeypatch):
    monkeypatch.setenv("CALLCOACH_TOOLS", "")
    monkeypatch.setenv("CALLCOACH_DATA", "~/elsewhere")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.TOOLS_DIR == reloaded.HOME / ".callcoach"
        assert reloaded.DATA_DIR == reloaded.HOME / "elsewhere"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_tool_skips_a_file_that_is_not_executable(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    stale = tmp_path / "bin" / "callcoach-test-tool"
    stale.write_text("")
    monkeypatch.setattr(config, "TOOLS_DIR", tmp_path)
    monkeypatch.setattr(config, "FALLBACK_BIN_DIRS", (), raising=False)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(FileNotFoundError):
        config.tool("callcoach-test-tool")
    os.chmod(stale, 0o755)
    assert config.tool("callcoach-test-tool") == str(stale)
