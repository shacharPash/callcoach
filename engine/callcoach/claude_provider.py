"""Claude through a subscription account: the Claude Agent SDK, one streaming session per call, no tools,
no settings, no MCP servers, and the account's own config folder. No token passes through this code.

Only the research step of `callcoach brief` gets tools, in its own options: the built-in web search and web fetch,
nothing else. It is given the request alone; the brief is written after it in a session with no tools."""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import dataclasses
import os
import threading
from pathlib import Path

from callcoach.providers import NoteBuffer, ProviderAuth, ProviderIncomplete, ProviderUnavailable, Result, Usage

REQUIRED_OPTIONS = ("tools", "setting_sources", "mcp_servers", "strict_mcp_config", "system_prompt", "model",
                    "max_turns", "env")
WEB_OPTIONS = ("allowed_tools", "permission_mode")
WEB_TOOLS = ("WebSearch", "WebFetch")
WEB_TURNS = 25
SCRUB_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "CLAUDECODE")
SCRUB_NAMES = ("CALLCOACH_API_KEY",)
LIMIT_STATES = {"allowed": "ok", "allowed_warning": "near", "rejected": "reached"}
AUTH_HINTS = ("authentication", "/login", "log in", "login", "invalid api key", "unauthorized", "oauth",
              "not logged in", "credential")


def scrub_environment(environ=os.environ) -> list[str]:
    """Removes inherited Claude and Anthropic variables. Returns their names (never their values)."""
    names = sorted(k for k in environ if k.startswith(SCRUB_PREFIXES) or k in SCRUB_NAMES)
    for key in names:
        environ.pop(key, None)
    return names


def classify(text: str) -> Exception:
    lower = text.lower()
    if any(h in lower for h in AUTH_HINTS):
        return ProviderAuth(text[:200])
    return ProviderUnavailable(text[:200] or "Claude did not answer")


class ClaudeProvider:
    name = "claude"

    def __init__(self, account_dir: Path, *, step_model: str, strong_model: str, sdk=None,
                 structured: bool = False, environ=os.environ, step_timeout: float = 15.0,
                 recap_timeout: float = 40.0, long_timeout: float = 180.0, brief_timeout: float = 600.0,
                 close_timeout: float = 10.0, connect_timeout: float = 75.0):
        if sdk is None:
            import claude_agent_sdk as sdk
        supported = {f.name for f in dataclasses.fields(sdk.ClaudeAgentOptions)}
        missing = [o for o in REQUIRED_OPTIONS if o not in supported]
        if missing:
            raise RuntimeError(f"this claude-agent-sdk has no {', '.join(missing)}; CallCoach needs it")
        self.scrubbed = scrub_environment(environ)
        self.sdk = sdk
        self.supported = supported
        self.account_dir = account_dir
        self.step_model = step_model
        self.strong_model = strong_model
        self.structured = structured and "output_format" in supported
        self.step_timeout = step_timeout
        self.recap_timeout = recap_timeout
        self.long_timeout = long_timeout
        self.brief_timeout = brief_timeout
        self.close_timeout = close_timeout
        # Starting a CLI child can take up to the SDK's own 60 s initialize limit, longer than a step may wait.
        self.connect_timeout = connect_timeout
        self.notes = NoteBuffer()
        # The subscription's limit as Claude last told it: "ok", "near" (allowed_warning) or "reached".
        self.limit = "ok"
        self._system = ""
        self._schema: dict | None = None
        self._client = None
        self._replace = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _options(self, system: str, model: str, schema: dict | None):
        fields = {"tools": [], "setting_sources": [], "mcp_servers": {}, "strict_mcp_config": True,
                  "system_prompt": system, "model": model, "max_turns": 1,
                  "env": {"CLAUDE_CONFIG_DIR": str(self.account_dir)}}
        if self.structured and schema:
            fields.update(output_format={"type": "json_schema", "schema": schema}, max_turns=2)
        return self.sdk.ClaudeAgentOptions(**fields)

    def _web_options(self, system: str):
        """The strong model with the built-in web search and web fetch only, allowed without asking, and every
        other tool refused; no settings and no MCP servers, as in every session."""
        missing = [o for o in WEB_OPTIONS if o not in self.supported]
        if missing:
            raise RuntimeError(f"this claude-agent-sdk has no {', '.join(missing)}; the research for a brief needs it")
        return self.sdk.ClaudeAgentOptions(
            tools=list(WEB_TOOLS), allowed_tools=list(WEB_TOOLS), permission_mode="dontAsk", setting_sources=[],
            mcp_servers={}, strict_mcp_config=True, system_prompt=system, model=self.strong_model,
            max_turns=WEB_TURNS, env={"CLAUDE_CONFIG_DIR": str(self.account_dir)})

    def _run(self, coro, timeout: float):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ProviderUnavailable(f"no answer in {timeout:.0f} s") from None

    async def _guard(self, coro):
        try:
            return await coro
        except (ProviderAuth, ProviderUnavailable):
            raise
        except Exception as e:
            raise classify(f"{type(e).__name__}: {e}") from e

    async def _connect(self) -> None:
        await self._disconnect()
        client = self.sdk.ClaudeSDKClient(options=self._options(self._system, self.step_model, self._schema))
        try:
            await client.connect()
        except BaseException:
            # A connect that fails or is cancelled by a timeout still started a CLI child: close it, so retries
            # do not leave one behind each time.
            self._client = client
            await self._disconnect()
            raise
        self._client = client

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client:
            try:
                await client.disconnect()
            except Exception as e:
                self.notes.add("provider_disconnect_failed", error=type(e).__name__, message=str(e)[:200])

    def open_call(self, system: str, schema: dict) -> None:
        self._system, self._schema = system, schema
        self._run(self._guard(self._connect()), self.long_timeout)

    def step(self, message: str) -> Result:
        try:
            must_connect = self._client is None or self._replace
            timeout = max(self.step_timeout, self.connect_timeout) if must_connect else self.step_timeout
            return self._run(self._step(message), timeout)
        except ProviderUnavailable:
            self._replace = True
            raise

    async def _step(self, message: str) -> Result:
        if self._client is None or self._replace:
            self._replace = False
            await self._guard(self._connect())

        async def exchange():
            await self._client.query(message)
            return [m async for m in self._client.receive_response()]

        try:
            messages = await self._guard(exchange())
        except (ProviderAuth, ProviderUnavailable):
            await self._disconnect()
            raise
        return self._collect(messages, self.step_model)

    async def _once(self, message: str, options, complete: bool = False) -> Result:
        async def run():
            return [m async for m in self.sdk.query(prompt=message, options=options)]

        return self._collect(await self._guard(run()), options.model, complete)

    def prepare(self, system: str, message: str, schema: dict) -> Result:
        return self._run(self._once(message, self._options(system, self.strong_model, schema)), self.long_timeout)

    def summarize(self, system: str, message: str) -> Result:
        return self._run(self._once(message, self._options(system, self.strong_model, None)), self.long_timeout)

    def explain(self, system: str, message: str) -> Result:
        """One request to the live model beside the call's session, which it never waits for."""
        return self._run(self._once(message, self._options(system, self.step_model, None)), self.step_timeout)

    def spot(self, system: str, message: str) -> Result:
        """The terms said a moment ago: one request to the live model beside the call's session, as explain."""
        return self._run(self._once(message, self._options(system, self.step_model, None)), self.step_timeout)

    def translate(self, system: str, message: str) -> Result:
        """English lines of the call in Hebrew: one request to the live model beside the call's session, as explain."""
        return self._run(self._once(message, self._options(system, self.step_model, None)), self.step_timeout)

    def look(self, system: str, message: str, image: bytes, media_type: str) -> Result:
        """What a screenshot of the call shows: one request with the image to the live model beside the call's
        session, as explain."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                 "data": base64.b64encode(image).decode()}},
                   {"type": "text", "text": message}]
        options = self._options(system, self.step_model, None)
        return self._run(self._once_content(content, options), self.step_timeout)

    async def _once_content(self, content: list[dict], options) -> Result:
        async def prompt():
            yield {"type": "user", "session_id": "", "parent_tool_use_id": None,
                   "message": {"role": "user", "content": content}}

        async def run():
            return [m async for m in self.sdk.query(prompt=prompt(), options=options)]

        return self._collect(await self._guard(run()), options.model)

    def recap(self, system: str, message: str) -> Result:
        """The recap of the call so far, on the strong model beside the call's session: asked rarely, read with care."""
        return self._run(self._once(message, self._options(system, self.strong_model, None)), self.recap_timeout)

    def research(self, system: str, message: str) -> Result:
        return self._run(self._once(message, self._web_options(system), complete=True), self.brief_timeout)

    def brief(self, system: str, message: str) -> Result:
        """The brief, written with no tools as the summary is."""
        options = self._options(system, self.strong_model, None)
        return self._run(self._once(message, options, complete=True), self.brief_timeout)

    def followup(self, system: str, message: str) -> Result:
        """The follow-up email of a call, written with no tools as the summary is; only a finished answer counts."""
        options = self._options(system, self.strong_model, None)
        return self._run(self._once(message, options, complete=True), self.long_timeout)

    def check(self) -> Result:
        options = self._options("Answer with the word OK.", self.step_model, None)
        return self._run(self._once("ping", options), max(self.step_timeout, self.connect_timeout))

    def close(self) -> None:
        try:
            self._run(self._disconnect(), self.close_timeout)
        except Exception as e:
            self.notes.add("provider_close_failed", error=type(e).__name__, message=str(e)[:200])
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def take_notes(self) -> list[tuple[str, dict]]:
        return self.notes.take()

    def _collect(self, messages: list, model: str, complete: bool = False) -> Result:
        """The answer. With `complete`, only an answer that ended on end_turn is one."""
        for m in messages:
            if type(m).__name__ == "RateLimitEvent":
                info = getattr(m, "rate_limit_info", None)
                status = info.get("status") if isinstance(info, dict) else getattr(info, "status", None)
                if status in LIMIT_STATES:
                    self.limit = LIMIT_STATES[status]
                if status == "rejected":
                    raise ProviderUnavailable("the subscription limit is reached")
        result = next((m for m in reversed(messages) if isinstance(m, self.sdk.ResultMessage)), None)
        assistant = [m for m in messages if isinstance(m, self.sdk.AssistantMessage)]
        texts = [getattr(b, "text", "") for m in assistant for b in m.content]
        errors = [str(m.error) for m in assistant if getattr(m, "error", None)]
        if result is None:
            raise ProviderUnavailable("no result from Claude")
        if result.is_error or errors:
            raise classify(" ".join([result.result or "", *errors, *texts]).strip())
        if complete and getattr(result, "stop_reason", None) not in (None, "end_turn"):
            raise ProviderIncomplete(f"Claude stopped with {result.stop_reason}")
        data = getattr(result, "structured_output", None)
        if data is None:
            data = result.result or "".join(texts)
        return Result(data, Usage.from_api(result.usage or {}), model)
