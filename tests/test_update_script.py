"""tools/update.sh against a local stand-in for GitHub: a bare repository with one commit more than the clone."""
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "update.sh"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, text):
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"add {name}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """(clone, the commit it is at, the newer commit on its origin)."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    old = commit(work, "a.txt", "1")
    new = commit(work, "b.txt", "2")
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(tmp_path / "origin.git")], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(clone)], check=True)
    git(clone, "reset", "-q", "--hard", old)
    return clone, old, new


def run(tmp_path, clone, setup="true"):
    """Runs the update for an app that has already quit; returns (result, how many times the app was opened)."""
    gone = subprocess.Popen(["true"])
    gone.wait()
    opened = tmp_path / "opened"
    opener = tmp_path / "opener.sh"
    opener.write_text(f'#!/bin/bash\necho "$1" >> "{opened}"\n')
    opener.chmod(0o755)
    result = tmp_path / "run" / "update-result.json"
    env = dict(os.environ, CALLCOACH_UPDATE_SETUP=setup, CALLCOACH_UPDATE_OPEN=str(opener))
    subprocess.run([str(SCRIPT), str(clone), "/Apps/CallCoach.app", str(gone.pid), str(result)], check=True, env=env,
                   capture_output=True, timeout=60)
    times = opened.read_text().splitlines() if opened.exists() else []
    assert times == ["/Apps/CallCoach.app"], "the app is opened once, whatever happened"
    return json.loads(result.read_text())


def test_moves_forward_runs_setup_and_opens_the_app(tmp_path, repos):
    clone, old, new = repos
    marker = tmp_path / "setup-ran"
    result = run(tmp_path, clone, setup=f"touch {marker}")
    assert result == {"ok": True, "step": "done", "from": old, "to": new}
    assert git(clone, "rev-parse", "HEAD") == new and marker.exists()


def test_changes_of_my_own_stop_it_before_anything_moves(tmp_path, repos):
    clone, old, _ = repos
    (clone / "a.txt").write_text("mine")
    result = run(tmp_path, clone)
    assert result["ok"] is False and result["step"] == "changes"
    assert git(clone, "rev-parse", "HEAD") == old and (clone / "a.txt").read_text() == "mine"


def test_a_file_of_my_own_that_git_does_not_track_is_no_change(tmp_path, repos):
    clone, _, new = repos
    (clone / "notes.txt").write_text("mine")
    assert run(tmp_path, clone)["ok"] is True
    assert git(clone, "rev-parse", "HEAD") == new and (clone / "notes.txt").read_text() == "mine"


def test_commits_of_my_own_are_never_pulled_over(tmp_path, repos):
    clone, _, _ = repos
    mine = commit(clone, "c.txt", "3")
    result = run(tmp_path, clone)
    assert result["ok"] is False and result["step"] == "diverged"
    assert git(clone, "rev-parse", "HEAD") == mine


def test_a_failed_setup_puts_the_clone_back(tmp_path, repos):
    clone, old, new = repos
    result = run(tmp_path, clone, setup="false")
    assert result == {"ok": False, "step": "setup", "from": old, "to": new}
    assert git(clone, "rev-parse", "HEAD") == old and not (clone / "b.txt").exists()


def test_another_branch_is_left_alone(tmp_path, repos):
    clone, old, _ = repos
    git(clone, "checkout", "-q", "-b", "mine")
    result = run(tmp_path, clone)
    assert result["ok"] is False and result["step"] == "branch"
    assert git(clone, "rev-parse", "HEAD") == old


def test_an_up_to_date_clone_is_done_without_moving(tmp_path, repos):
    clone, _, new = repos
    git(clone, "reset", "-q", "--hard", new)
    assert run(tmp_path, clone) == {"ok": True, "step": "done", "from": new, "to": new}
