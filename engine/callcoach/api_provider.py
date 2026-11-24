"""Claude through the official Anthropic SDK and a personal API key. No Claude Code, no CLI, no hooks."""
from __future__ import annotations

import base64
import os

from callcoach.claude_provider import scrub_environment
from callcoach.providers import NoteBuffer, ProviderAuth, ProviderIncomplete, ProviderUnavailable, Result, Usage

API_URL = "https://api.anthropic.com"
MAX_PAIRS = 40
KEEP_PAIRS = 20
WEB_TOOLS = [{"type": "web_search_20260209", "name": "web_search"},
             {"type": "web_fetch_20260209", "name": "web_fetch"}]
MAX_CONTINUATIONS = 5
# A step's answer: up to 5 terms with Hebrew notes, the now line, two questions and the one asked of me with their
# translations, a card and goals.
STEP_MAX_TOKENS = 800
FOLLOWUP_MAX_TOKENS = 8000


def thinking_for(model: str) -> dict:
    return {"type": "disabled"} if "haiku" in model else {"type": "adaptive"}


FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _text(resp) -> str:
    if getattr(resp, "stop_reason", None) == "refusal":
        return ""
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")


def _last_text(resp) -> str:
    """The text after the last block that is not text: with web tools, what the model wrote once its research was
    done. Citations split it into several text blocks, joined here."""
    texts: list[str] = []
    for b in resp.content:
        if getattr(b, "type", "") == "text":
            texts.append(getattr(b, "text", ""))
        else:
            texts = []
    return "".join(texts)


def _finished(resp) -> None:
    """Only end_turn is a finished answer: one cut at max_tokens, a refusal or any other stop is not."""
    reason = getattr(resp, "stop_reason", None)
    if reason != "end_turn":
        raise ProviderIncomplete(f"the model stopped with {reason}")


class InvalidRequest(ProviderUnavailable):
    """A 400 from the API, for example a conversation that no longer fits."""


def map_error(e: Exception) -> Exception:
    import anthropic

    message = getattr(e, "message", None) or str(e) or type(e).__name__
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderAuth(message)
    if isinstance(e, anthropic.BadRequestError):
        return InvalidRequest(f"400: {message}")
    if isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return ProviderUnavailable(message)
    if isinstance(e, anthropic.APIStatusError):
        return ProviderUnavailable(f"{getattr(e, 'status_code', '?')}: {message}")
    return e


class ApiProvider:
    """Steps share one conversation: the cached system prefix plus the most recent pairs. Past MAX_PAIRS
    pairs (a step and its reply are about 700 tokens, so about 30K tokens with the prefix) the oldest are
    dropped down to KEEP_PAIRS at once, so the cached prefix breaks only once every KEEP_PAIRS steps."""

    name = "api"

    def __init__(self, api_key: str | None, *, step_model: str, strong_model: str, client=None,
                 step_timeout: float = 15.0, recap_timeout: float = 40.0, long_timeout: float = 180.0,
                 brief_timeout: float = 600.0,
                 environ=os.environ):
        self.scrubbed = scrub_environment(environ)
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, base_url=API_URL, max_retries=0)
        self.client = client
        self.step_model = step_model
        self.strong_model = strong_model
        self.step_timeout = step_timeout
        self.recap_timeout = recap_timeout
        self.long_timeout = long_timeout
        self.brief_timeout = brief_timeout
        self.system = ""
        self.schema: dict | None = None
        self.messages: list[dict] = []
        self.notes = NoteBuffer()

    def open_call(self, system: str, schema: dict) -> None:
        self.system, self.schema, self.messages = system, schema, []

    def step(self, message: str) -> Result:
        if len(self.messages) > 2 * MAX_PAIRS:
            self.messages = self.messages[-2 * KEEP_PAIRS:]
        try:
            return self._turn(message)
        except InvalidRequest as e:
            self.notes.add("conversation_reset", message=str(e)[:200])
            self.messages = []
            return self._turn(message)

    def _turn(self, message: str) -> Result:
        self.messages.append({"role": "user", "content": message})
        try:
            resp = self._create(self.step_model, self.system, self.messages, self.schema, STEP_MAX_TOKENS,
                                self.step_timeout)
        except Exception:
            self.messages.pop()
            raise
        text = _text(resp)
        self.messages.append({"role": "assistant", "content": text or "{}"})
        return Result(text, Usage.from_api(resp.usage), self.step_model)

    def prepare(self, system: str, message: str, schema: dict) -> Result:
        resp = self._create(self.strong_model, system, [{"role": "user", "content": message}], schema, 2000,
                            self.long_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.strong_model)

    def summarize(self, system: str, message: str) -> Result:
        resp = self._create(self.strong_model, system, [{"role": "user", "content": message}], None, 8000,
                            self.long_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.strong_model)

    def explain(self, system: str, message: str) -> Result:
        resp = self._create(self.step_model, system, [{"role": "user", "content": message}], None, 600,
                            self.step_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.step_model)

    def spot(self, system: str, message: str) -> Result:
        """The terms said a moment ago, on the live model."""
        resp = self._create(self.step_model, system, [{"role": "user", "content": message}], None, 600,
                            self.step_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.step_model)

    def translate(self, system: str, message: str) -> Result:
        """English lines of the call in Hebrew, on the live model."""
        resp = self._create(self.step_model, system, [{"role": "user", "content": message}], None, 600,
                            self.step_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.step_model)

    def look(self, system: str, message: str, image: bytes, media_type: str) -> Result:
        """What a screenshot of the call shows, on the live model."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                 "data": base64.b64encode(image).decode()}},
                   {"type": "text", "text": message}]
        resp = self._create(self.step_model, system, [{"role": "user", "content": content}], None, 600,
                            self.step_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.step_model)

    def recap(self, system: str, message: str) -> Result:
        """The recap of the call so far, on the strong model: asked rarely, read with care."""
        resp = self._create(self.strong_model, system, [{"role": "user", "content": message}], None, 800,
                            self.recap_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.strong_model)

    def research(self, system: str, message: str) -> Result:
        """The strong model with the server's web search and web fetch. A turn the server paused (its own loop of
        tool calls reached its limit) is sent back as it is, and the server goes on from there."""
        messages: list[dict] = [{"role": "user", "content": message}]
        usage = Usage()
        for _ in range(MAX_CONTINUATIONS + 1):
            resp = self._create(self.strong_model, system, messages, None, 16000, self.brief_timeout,
                                tools=WEB_TOOLS, effort="medium")
            usage = usage + Usage.from_api(resp.usage)
            if getattr(resp, "stop_reason", None) != "pause_turn":
                _finished(resp)
                return Result(_last_text(resp), usage, self.strong_model)
            messages.append({"role": "assistant", "content": resp.content})
        raise ProviderIncomplete(f"the research was still not done after {MAX_CONTINUATIONS} continuations")

    def brief(self, system: str, message: str) -> Result:
        """The brief, written with no tools as the summary is."""
        resp = self._create(self.strong_model, system, [{"role": "user", "content": message}], None, 16000,
                            self.brief_timeout)
        _finished(resp)
        return Result(_text(resp), Usage.from_api(resp.usage), self.strong_model)

    def followup(self, system: str, message: str) -> Result:
        """The follow-up email of a call, written with no tools as the summary is; only a finished answer counts."""
        resp = self._create(self.strong_model, system, [{"role": "user", "content": message}], None,
                            FOLLOWUP_MAX_TOKENS, self.long_timeout)
        _finished(resp)
        return Result(_text(resp), Usage.from_api(resp.usage), self.strong_model)

    def check(self) -> Result:
        resp = self._create(self.step_model, "Answer with the word OK.", [{"role": "user", "content": "ping"}],
                            None, 5, self.step_timeout)
        return Result(_text(resp), Usage.from_api(resp.usage), self.step_model)

    def close(self) -> None:
        pass

    def take_notes(self) -> list[tuple[str, dict]]:
        return self.notes.take()

    def _create(self, model: str, system: str, messages: list[dict], schema: dict | None, max_tokens: int,
                timeout: float, *, tools: list | None = None, effort: str = "low"):
        output: dict = {"effort": effort}
        if schema:
            output["format"] = {"type": "json_schema", "schema": schema}
        request = dict(model=model, max_tokens=max_tokens,
                       system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                       messages=messages, cache_control={"type": "ephemeral"}, thinking=thinking_for(model),
                       output_config=output)
        if tools:
            request["tools"] = tools
        client = self.client.with_options(timeout=timeout)
        try:
            if "sonnet" in model:
                return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            return client.messages.create(**request)
        except Exception as e:
            mapped = map_error(e)
            if mapped is e:
                raise
            raise mapped from e
