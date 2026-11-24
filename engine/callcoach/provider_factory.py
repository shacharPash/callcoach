"""The provider for this engine process, from the environment the app sets, and the account commands."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from callcoach.claude_provider import scrub_environment
from callcoach.config import TOOLS_DIR
from callcoach.providers import FakeProvider, ProviderAuth

ACCOUNTS_DIR = TOOLS_DIR / "accounts"
PROVIDERS = ("claude", "api", "fake", "none")
STEP_MODEL = "claude-haiku-5-5"
STRONG_MODEL = "claude-sonnet-5-5"
LOGIN_FLAGS = {"subscription": [], "console": ["--console"], "sso": ["--sso"]}
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Account:
    provider: str
    name: str
    folder: Path | None = None

    @property
    def via_claude_code(self) -> bool:
        return self.provider == "claude"


def account_folder(name: str, accounts: Path = ACCOUNTS_DIR) -> Path:
    if not _NAME.match(name):
        raise ValueError(f"an account name is lowercase letters, digits, - and _ (got {name!r})")
    return accounts / name


def read_account(environ=os.environ) -> tuple[Account, str | None]:
    """Reads the account and removes the API key from the environment before anything else runs."""
    key = environ.pop("CALLCOACH_API_KEY", None) or None
    provider = (environ.get("CALLCOACH_PROVIDER") or "none").strip()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown CALLCOACH_PROVIDER {provider!r}")
    name = (environ.get("CALLCOACH_ACCOUNT_NAME") or provider).strip()
    folder = environ.get("CALLCOACH_ACCOUNT_DIR")
    return Account(provider, name, Path(folder).expanduser() if folder else None), key


def build_provider(account: Account, key: str | None, *, step_model: str = STEP_MODEL,
                   strong_model: str = STRONG_MODEL, claude_cls=None, api_cls=None):
    if account.provider == "none":
        return None
    if account.provider == "fake":
        return FakeProvider()
    if account.provider == "api":
        if not key:
            raise ProviderAuth(f"account {account.name} has no API key")
        if api_cls is None:
            from callcoach.api_provider import ApiProvider as api_cls
        return api_cls(key, step_model=step_model, strong_model=strong_model)
    if account.folder is None or not account.folder.is_dir():
        raise ProviderAuth(f"account {account.name} is not logged in yet")
    if claude_cls is None:
        from callcoach.claude_provider import ClaudeProvider as claude_cls
    return claude_cls(account.folder, step_model=step_model, strong_model=strong_model)


def bundled_cli() -> Path:
    import claude_agent_sdk

    root = Path(claude_agent_sdk.__file__).resolve().parent
    for path in sorted(root.rglob("claude")):
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise FileNotFoundError("the Claude CLI bundled with claude-agent-sdk is missing; run ./setup.sh")


def _clean_env(environ, folder: Path) -> dict:
    env = dict(environ)
    scrub_environment(env)
    env["CLAUDE_CONFIG_DIR"] = str(folder)
    return env


def login_command(name: str, mode: str = "subscription", *, cli: Path, accounts: Path = ACCOUNTS_DIR,
                  environ=os.environ) -> tuple[list[str], dict, Path]:
    folder = account_folder(name, accounts)
    return [str(cli), "auth", "login", *LOGIN_FLAGS[mode]], _clean_env(environ, folder), folder


def _logged_in(text: str) -> bool:
    try:
        return bool(json.loads(text).get("loggedIn"))
    except (ValueError, AttributeError):
        lower = text.lower()
        return "logged in" in lower and "not logged in" not in lower


def account_status(name: str, *, cli: Path, accounts: Path = ACCOUNTS_DIR, environ=os.environ,
                   run=subprocess.run) -> dict:
    """Logged in or not. Never returns anything else from the CLI's output (no email, no organization)."""
    folder = account_folder(name, accounts)
    if not folder.is_dir():
        return {"name": name, "logged_in": False, "message": "not logged in yet"}
    try:
        proc = run([str(cli), "auth", "status"], env=_clean_env(environ, folder), capture_output=True, text=True,
                   timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"name": name, "logged_in": False, "message": f"status check failed ({type(e).__name__})"}
    ok = _logged_in(proc.stdout or "")
    return {"name": name, "logged_in": ok, "message": "" if ok else "not logged in"}


def account_logout(name: str, *, cli: Path, accounts: Path = ACCOUNTS_DIR, environ=os.environ,
                   run=subprocess.run) -> dict:
    """Logs the account folder out, which removes the login the CLI keeps in the Keychain. Says only whether that
    worked, never what the CLI printed."""
    folder = account_folder(name, accounts)
    if not folder.is_dir():
        return {"name": name, "logged_out": True, "message": "not logged in yet"}
    try:
        proc = run([str(cli), "auth", "logout"], env=_clean_env(environ, folder), capture_output=True, text=True,
                   timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"name": name, "logged_out": False, "message": f"logout failed ({type(e).__name__})"}
    if proc.returncode != 0:
        return {"name": name, "logged_out": False, "message": f"logout failed (exit {proc.returncode})"}
    return {"name": name, "logged_out": True, "message": ""}
