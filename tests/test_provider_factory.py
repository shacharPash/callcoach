import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from callcoach.provider_factory import (Account, account_folder, account_logout, account_status, build_provider,
                                        bundled_cli, login_command, read_account)
from callcoach.providers import FakeProvider, ProviderAuth


def test_the_default_is_no_agent():
    account, key = read_account({})
    assert account == Account("none", "none", None) and key is None and not account.via_claude_code


def test_the_api_key_leaves_the_environment_at_once():
    env = {"CALLCOACH_PROVIDER": "api", "CALLCOACH_ACCOUNT_NAME": "home", "CALLCOACH_API_KEY": "k"}
    account, key = read_account(env)
    assert account == Account("api", "home", None) and key == "k" and "CALLCOACH_API_KEY" not in env


def test_a_subscription_account_runs_through_claude_code():
    env = {"CALLCOACH_PROVIDER": "claude", "CALLCOACH_ACCOUNT_NAME": "work", "CALLCOACH_ACCOUNT_DIR": "/acc/work"}
    account, _ = read_account(env)
    assert account.folder == Path("/acc/work") and account.via_claude_code


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError):
        read_account({"CALLCOACH_PROVIDER": "openai"})


@pytest.mark.parametrize("name", ["../x", "Work", "", "a" * 40, "a b"])
def test_account_names_stay_inside_the_accounts_folder(name, tmp_path):
    with pytest.raises(ValueError):
        account_folder(name, tmp_path)


def test_build_provider_by_kind(tmp_path):
    made = []

    def claude_cls(folder, **kw):
        made.append(("claude", folder, kw["step_model"]))
        return "claude-provider"

    def api_cls(key, **kw):
        made.append(("api", key, kw["strong_model"]))
        return "api-provider"

    kinds = dict(claude_cls=claude_cls, api_cls=api_cls, step_model="h", strong_model="s")
    assert build_provider(Account("none", "none"), None, **kinds) is None
    assert isinstance(build_provider(Account("fake", "fake"), None, **kinds), FakeProvider)
    assert build_provider(Account("api", "home"), "k", **kinds) == "api-provider"
    assert build_provider(Account("claude", "work", tmp_path), None, **kinds) == "claude-provider"
    assert made == [("api", "k", "s"), ("claude", tmp_path, "h")]


def test_a_missing_key_or_folder_is_an_auth_problem(tmp_path):
    with pytest.raises(ProviderAuth):
        build_provider(Account("api", "home"), None, step_model="h", strong_model="s")
    with pytest.raises(ProviderAuth):
        build_provider(Account("claude", "work", tmp_path / "missing"), None, step_model="h", strong_model="s")


def test_login_runs_the_bundled_cli_in_the_account_folder_with_a_clean_environment(tmp_path):
    env = {"ANTHROPIC_BASE_URL": "x", "CLAUDE_CODE_OAUTH_SCOPES": "x", "PATH": "/usr/bin", "HOME": "/h"}
    argv, child_env, folder = login_command("work", "sso", cli=Path("/cli/claude"), accounts=tmp_path, environ=env)
    assert argv == ["/cli/claude", "auth", "login", "--sso"] and folder == tmp_path / "work"
    assert child_env == {"PATH": "/usr/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": str(tmp_path / "work")}
    assert "ANTHROPIC_BASE_URL" in env


def test_status_reports_only_whether_the_account_is_logged_in(tmp_path):
    (tmp_path / "work").mkdir()
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw["env"]["CLAUDE_CONFIG_DIR"]))
        return SimpleNamespace(stdout=json.dumps({"loggedIn": True, "email": "someone@example.com"}), returncode=0)

    status = account_status("work", cli=Path("/cli/claude"), accounts=tmp_path, environ={}, run=run)
    assert status == {"name": "work", "logged_in": True, "message": ""}
    assert calls == [(["/cli/claude", "auth", "status"], str(tmp_path / "work"))]


def test_status_of_an_account_that_never_logged_in(tmp_path):
    status = account_status("home", cli=Path("/cli/claude"), accounts=tmp_path, environ={})
    assert status == {"name": "home", "logged_in": False, "message": "not logged in yet"}


def test_status_understands_plain_text(tmp_path):
    (tmp_path / "work").mkdir()
    run = lambda argv, **kw: SimpleNamespace(stdout="Not logged in. Run claude auth login.", returncode=1)
    assert account_status("work", cli=Path("/c"), accounts=tmp_path, environ={}, run=run)["logged_in"] is False


def test_logout_runs_the_bundled_cli_in_the_account_folder_with_a_clean_environment(tmp_path):
    (tmp_path / "work").mkdir()
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw["env"], kw["stdin"]))
        return SimpleNamespace(stdout="Successfully logged out from your Anthropic account.", returncode=0)

    env = {"ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_OAUTH_TOKEN": "x", "PATH": "/usr/bin", "HOME": "/h"}
    reply = account_logout("work", cli=Path("/cli/claude"), accounts=tmp_path, environ=env, run=run)
    assert reply == {"name": "work", "logged_out": True, "message": ""}
    assert calls == [(["/cli/claude", "auth", "logout"],
                      {"PATH": "/usr/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": str(tmp_path / "work")},
                      subprocess.DEVNULL)]


def test_logout_of_an_account_that_never_logged_in_runs_nothing(tmp_path):
    def run(argv, **kw):
        raise AssertionError("the CLI ran")

    reply = account_logout("home", cli=Path("/cli/claude"), accounts=tmp_path, environ={}, run=run)
    assert reply == {"name": "home", "logged_out": True, "message": "not logged in yet"}
    assert not (tmp_path / "home").exists()


def test_a_failed_logout_says_so_without_the_cli_output(tmp_path):
    (tmp_path / "work").mkdir()
    run = lambda argv, **kw: SimpleNamespace(stdout="someone@example.com", stderr="someone@example.com", returncode=1)
    reply = account_logout("work", cli=Path("/c"), accounts=tmp_path, environ={}, run=run)
    assert reply == {"name": "work", "logged_out": False, "message": "logout failed (exit 1)"}


@pytest.mark.parametrize("error", [OSError("no cli"), subprocess.TimeoutExpired("claude", 30)])
def test_a_logout_that_cannot_run(error, tmp_path):
    (tmp_path / "work").mkdir()

    def run(argv, **kw):
        raise error

    reply = account_logout("work", cli=Path("/c"), accounts=tmp_path, environ={}, run=run)
    assert reply["logged_out"] is False and reply["message"] == f"logout failed ({type(error).__name__})"


def test_the_bundled_cli_is_found():
    assert bundled_cli().name == "claude"
