import asyncio
import dataclasses
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from callcoach.claude_provider import ClaudeProvider, scrub_environment
from callcoach.providers import ProviderAuth, ProviderIncomplete, ProviderUnavailable, Usage


@dataclasses.dataclass
class Options:
    tools: list | None = None
    allowed_tools: list = dataclasses.field(default_factory=list)
    permission_mode: str | None = None
    setting_sources: list | None = None
    mcp_servers: dict | None = None
    strict_mcp_config: bool = False
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    env: dict = dataclasses.field(default_factory=dict)
    output_format: dict | None = None


@dataclasses.dataclass
class StrictOptions:
    tools: list | None = None
    setting_sources: list | None = None
    mcp_servers: dict | None = None
    strict_mcp_config: bool = False
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    env: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class OldOptions:
    tools: list | None = None
    setting_sources: list | None = None
    mcp_servers: dict | None = None
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    env: dict = dataclasses.field(default_factory=dict)


class ResultMessage:
    def __init__(self, structured_output=None, result="", is_error=False, usage=None, stop_reason=None):
        self.structured_output = structured_output
        self.stop_reason = stop_reason
        self.result = result
        self.is_error = is_error
        self.usage = usage or {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 800}


class AssistantMessage:
    def __init__(self, text, error=None):
        self.content = [SimpleNamespace(text=text)]
        self.error = error


class RateLimitEvent:
    def __init__(self, status):
        self.rate_limit_info = {"status": status}


def fake_sdk(steps, once=(), options=Options, disconnect_error=None, disconnect_delay=0.0, connect_error=None,
             connect_delay=0.0, query_delay=0.0):
    state = SimpleNamespace(connects=0, disconnects=0, options=[], queries=[], prompts=[])
    steps, once = list(steps), list(once)

    class Client:
        def __init__(self, options):
            state.options.append(options)

        async def connect(self):
            state.connects += 1
            if connect_delay:
                await asyncio.sleep(connect_delay)
            if connect_error:
                raise connect_error

        async def disconnect(self):
            state.disconnects += 1
            if disconnect_delay:
                await asyncio.sleep(disconnect_delay)
            if disconnect_error:
                raise disconnect_error

        async def query(self, message):
            state.queries.append(message)

        async def receive_response(self):
            item = steps.pop(0)
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
                item = []
            for m in item:
                yield m

    async def query(prompt, options):
        state.options.append(options)
        if query_delay:
            await asyncio.sleep(query_delay)
        if not isinstance(prompt, str):
            state.prompts.append([m async for m in prompt])
        for m in once.pop(0):
            yield m

    sdk = SimpleNamespace(ClaudeAgentOptions=options, ClaudeSDKClient=Client, ResultMessage=ResultMessage,
                          AssistantMessage=AssistantMessage, query=query)
    return sdk, state


def make(steps, once=(), timeout=2.0, disconnect_error=None, disconnect_delay=0.0, connect_error=None,
         connect_delay=0.0, query_delay=0.0, **kw):
    sdk, state = fake_sdk(steps, once, disconnect_error=disconnect_error, disconnect_delay=disconnect_delay,
                          connect_error=connect_error, connect_delay=connect_delay, query_delay=query_delay)
    p = ClaudeProvider(Path("/acc/work"), step_model="claude-haiku-5-5", strong_model="claude-sonnet-5-5",
                       sdk=sdk, step_timeout=timeout, environ={}, **kw)
    return p, state


REPLY = '{"card": null, "goals": []}'


def test_a_session_has_no_tools_no_settings_no_mcp_and_the_account_folder():
    p, state = make([[ResultMessage(result=REPLY)]])
    p.open_call("rules", {"type": "object"})
    result = p.step("line")
    o = state.options[0]
    assert (o.tools, o.setting_sources, o.mcp_servers, o.strict_mcp_config, o.system_prompt, o.model) == \
        ([], [], {}, True, "rules", "claude-haiku-5-5")
    assert o.env == {"CLAUDE_CONFIG_DIR": "/acc/work"} and o.max_turns == 1 and o.output_format is None
    assert o.allowed_tools == [] and o.permission_mode is None
    assert result.data == REPLY and result.usage == Usage(10, 2, 800, 0, 1)
    p.close()


def test_the_structured_switch_uses_output_format_and_two_turns():
    p, state = make([[ResultMessage(structured_output={"card": None, "goals": []})]], structured=True)
    p.open_call("rules", {"type": "object"})
    assert p.step("line").data == {"card": None, "goals": []}
    o = state.options[0]
    assert o.output_format == {"type": "json_schema", "schema": {"type": "object"}} and o.max_turns == 2
    p.close()


def test_an_sdk_without_strict_mcp_config_is_refused():
    sdk, _ = fake_sdk([], options=OldOptions)
    with pytest.raises(RuntimeError, match="strict_mcp_config"):
        ClaudeProvider(Path("/acc/work"), step_model="h", strong_model="s", sdk=sdk, environ={})


def test_the_assistant_text_is_used_when_the_result_is_empty():
    p, _ = make([[AssistantMessage(REPLY), ResultMessage(result="")]])
    p.open_call("rules", {})
    assert p.step("x").data == REPLY
    p.close()


def test_a_login_problem_is_an_auth_error():
    p, _ = make([[ResultMessage(is_error=True, result="Invalid API key. Please run /login")]])
    p.open_call("rules", {})
    with pytest.raises(ProviderAuth):
        p.step("x")
    p.close()


def test_a_rejected_rate_limit_is_unavailable_and_an_allowed_one_is_ignored():
    p, _ = make([[RateLimitEvent("rejected"), ResultMessage(is_error=True, result="limit")],
                 [RateLimitEvent("allowed"), ResultMessage(result=REPLY)]])
    p.open_call("rules", {})
    with pytest.raises(ProviderUnavailable):
        p.step("x")
    assert p.step("y").data == REPLY
    p.close()


def test_a_step_that_times_out_replaces_the_session():
    p, state = make([5.0, [ResultMessage(result=REPLY)]], timeout=0.2)
    p.open_call("rules", {})
    with pytest.raises(ProviderUnavailable):
        p.step("slow")
    assert p.step("fast").data == REPLY
    assert state.connects == 2 and state.disconnects >= 1
    p.close()


def test_prepare_uses_the_strong_model_once():
    p, state = make([], once=[[ResultMessage(result='{"goals": [], "questions": [], "glossary": []}')]])
    p.prepare("prep rules", "notes", {"type": "object"})
    o = state.options[0]
    assert o.model == "claude-sonnet-5-5" and o.tools == [] and o.strict_mcp_config is True
    assert o.output_format is None and o.max_turns == 1
    p.close()


def test_the_environment_is_scrubbed_of_every_claude_and_anthropic_variable():
    env = {"ANTHROPIC_BASE_URL": "x", "ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_OAUTH_SCOPES": "x",
           "CLAUDE_CODE_OAUTH_TOKEN": "x", "CLAUDECODE": "1", "CLAUDE_CONFIG_DIR": "x", "CALLCOACH_API_KEY": "x",
           "PATH": "/usr/bin", "CALLCOACH_ACCOUNT_DIR": "/acc/work"}
    removed = scrub_environment(env)
    assert env == {"PATH": "/usr/bin", "CALLCOACH_ACCOUNT_DIR": "/acc/work"}
    assert "ANTHROPIC_BASE_URL" in removed and "CLAUDE_CODE_OAUTH_SCOPES" in removed


def test_the_provider_scrubs_before_its_first_client():
    sdk, _ = fake_sdk([])
    env = {"ANTHROPIC_BASE_URL": "x", "HOME": "/h"}
    p = ClaudeProvider(Path("/acc/work"), step_model="h", strong_model="s", sdk=sdk, environ=env)
    assert env == {"HOME": "/h"}
    p.close()


def test_a_connect_that_fails_closes_its_client():
    p, state = make([], connect_error=Exception("Control request timeout: initialize"))
    with pytest.raises((ProviderAuth, ProviderUnavailable)):
        p.open_call("rules", {"type": "object"})
    assert (state.connects, state.disconnects) == (1, 1)
    p.close()


def test_a_connect_cut_short_by_its_timeout_closes_its_client():
    p, state = make([], timeout=0.2, connect_delay=5.0, connect_timeout=0.2)
    with pytest.raises(ProviderUnavailable):
        p.step("line")
    deadline = time.monotonic() + 2.0
    while state.disconnects == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (state.connects, state.disconnects) == (1, 1)
    p.close()


def test_a_step_that_must_connect_first_waits_for_the_connect_timeout():
    p, state = make([[ResultMessage(result=REPLY)]], timeout=0.2, connect_delay=0.5, connect_timeout=3.0)
    p.open_call("rules", {"type": "object"})
    p._replace = True
    assert p.step("line").data == REPLY and state.connects == 2
    p.close()


def test_a_step_on_an_open_session_keeps_the_short_timeout():
    p, state = make([0.5], timeout=0.2, connect_timeout=3.0)
    p.open_call("rules", {"type": "object"})
    with pytest.raises(ProviderUnavailable):
        p.step("line")
    p.close()


def test_the_account_check_waits_for_a_cold_start():
    p, state = make([], once=[[ResultMessage(result="OK")]], timeout=0.2, query_delay=0.5, connect_timeout=3.0)
    assert p.check().data == "OK"
    p.close()


def test_a_failed_disconnect_is_kept_as_a_note():
    p, state = make([5.0, [ResultMessage(result=REPLY)]], timeout=0.2,
                    disconnect_error=RuntimeError("child still running"))
    p.open_call("rules", {})
    with pytest.raises(ProviderUnavailable):
        p.step("slow")
    assert p.step("fast").data == REPLY
    p.close()
    notes = p.take_notes()
    assert notes and all(n == ("provider_disconnect_failed", {"error": "RuntimeError", "message": "child still running"})
                         for n in notes)
    assert p.take_notes() == []


def test_a_close_that_times_out_is_kept_as_a_note():
    p, _ = make([[ResultMessage(result=REPLY)]], disconnect_delay=2.0, close_timeout=0.1)
    p.open_call("rules", {})
    p.step("x")
    p.close()
    [(kind, fields)] = p.take_notes()
    assert kind == "provider_close_failed" and fields["error"] == "ProviderUnavailable"


def test_an_explanation_is_one_request_to_the_live_model_beside_the_session():
    p, state = make([[ResultMessage(result=REPLY)]], once=[[ResultMessage(result="הסבר קצר")]])
    p.open_call("rules", {})
    p.step("line")
    result = p.explain("explain rules", "term: Okta")
    assert result.data == "הסבר קצר" and result.model == "claude-haiku-5-5"
    o = state.options[-1]
    assert (o.tools, o.allowed_tools, o.setting_sources, o.mcp_servers, o.strict_mcp_config, o.model, o.max_turns) == \
        ([], [], [], {}, True, "claude-haiku-5-5", 1)
    assert state.connects == 1 and state.queries == ["line"]
    p.close()


def test_a_translation_is_one_request_to_the_live_model_beside_the_session():
    p, state = make([[ResultMessage(result=REPLY)]], once=[[ResultMessage(result='{"lines": []}')]])
    p.open_call("rules", {})
    p.step("line")
    result = p.translate("translate rules", "## Translate\n1: hello")
    assert result.data == '{"lines": []}' and result.model == "claude-haiku-5-5"
    assert state.options[-1].model == "claude-haiku-5-5" and state.queries == ["line"]
    p.close()


def test_a_screenshot_is_one_request_with_the_image_to_the_live_model():
    p, state = make([[ResultMessage(result=REPLY)]], once=[[ResultMessage(result='{"screen": "שקף"}')]])
    p.open_call("rules", {})
    result = p.look("screen rules", "## The call", b"png bytes", "image/png")
    assert result.data == '{"screen": "שקף"}' and result.model == "claude-haiku-5-5"
    [[message]] = state.prompts
    image, text = message["message"]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "cG5nIGJ5dGVz"}}
    assert text == {"type": "text", "text": "## The call"} and message["type"] == "user"
    assert (state.options[-1].tools, state.options[-1].model, state.options[-1].max_turns) == ([], "claude-haiku-5-5", 1)
    p.close()


def test_a_recap_is_one_request_to_the_strong_model_beside_the_session():
    p, state = make([[ResultMessage(result=REPLY)]], once=[[ResultMessage(result="- שורה")]])
    p.open_call("rules", {})
    p.step("line")
    result = p.recap("recap rules", "## Transcript")
    assert result.data == "- שורה" and result.model == "claude-sonnet-5-5"
    o = state.options[-1]
    assert (o.tools, o.allowed_tools, o.setting_sources, o.mcp_servers, o.model, o.max_turns) == \
        ([], [], [], {}, "claude-sonnet-5-5", 1)
    assert state.connects == 1 and state.queries == ["line"]
    p.close()


def test_the_research_gets_web_search_and_web_fetch_and_nothing_else():
    p, state = make([], once=[[AssistantMessage("I will search."), ResultMessage(result="## People")]])
    result = p.research("brief rules", "request")
    o = state.options[0]
    assert o.tools == ["WebSearch", "WebFetch"] and o.allowed_tools == ["WebSearch", "WebFetch"]
    assert o.permission_mode == "dontAsk" and o.setting_sources == [] and o.mcp_servers == {}
    assert o.strict_mcp_config is True and o.model == "claude-sonnet-5-5" and o.system_prompt == "brief rules"
    assert o.env == {"CLAUDE_CONFIG_DIR": "/acc/work"} and 1 < o.max_turns <= 30 and o.output_format is None
    assert result.data == "## People" and result.model == "claude-sonnet-5-5"
    p.close()


def test_the_brief_is_written_with_no_tools_as_the_summary_is():
    p, state = make([], once=[[ResultMessage(result="---\ntitle: x\n---\n", stop_reason="end_turn")],
                              [ResultMessage(result="## סיכום")]])
    assert p.brief("brief rules", "notes").data == "---\ntitle: x\n---\n"
    p.summarize("summary rules", "transcript")
    written, summary = state.options
    assert written == dataclasses.replace(summary, system_prompt="brief rules")
    assert written.tools == [] and written.allowed_tools == [] and written.permission_mode is None
    assert written.max_turns == 1 and written.model == "claude-sonnet-5-5"
    p.close()


def test_the_followup_is_written_with_no_tools_as_the_summary_is():
    p, state = make([], once=[[ResultMessage(result="Subject: x\n\nbody", stop_reason="end_turn")],
                              [ResultMessage(result="## סיכום")]])
    assert p.followup("followup rules", "transcript").data == "Subject: x\n\nbody"
    p.summarize("summary rules", "transcript")
    written, summary = state.options
    assert written == dataclasses.replace(summary, system_prompt="followup rules")
    assert written.tools == [] and written.model == "claude-sonnet-5-5"
    p.close()


@pytest.mark.parametrize("step", ["brief", "research", "followup"])
def test_an_answer_cut_at_max_tokens_is_never_a_brief_or_research(step):
    p, state = make([], once=[[ResultMessage(result="---\ntitle: x", stop_reason="max_tokens")]])
    with pytest.raises(ProviderIncomplete, match="max_tokens"):
        getattr(p, step)("rules", "request")
    p.close()


def test_the_call_session_keeps_no_tools_after_a_research():
    p, state = make([[ResultMessage(result=REPLY)]], once=[[ResultMessage(result="## People")]])
    p.research("research rules", "request")
    p.open_call("rules", {})
    p.step("line")
    o = state.options[-1]
    assert o.tools == [] and o.allowed_tools == [] and o.permission_mode is None
    p.close()


def test_the_research_needs_an_sdk_that_can_allow_tools():
    sdk, _ = fake_sdk([], options=StrictOptions)
    p = ClaudeProvider(Path("/acc/work"), step_model="h", strong_model="s", sdk=sdk, environ={})
    with pytest.raises(RuntimeError, match="allowed_tools"):
        p.research("research rules", "request")
    p.close()


def test_the_limit_state_follows_what_claude_says():
    p, _ = make([[RateLimitEvent("allowed_warning"), ResultMessage(result=REPLY)],
                 [RateLimitEvent("rejected"), ResultMessage(is_error=True, result="limit")],
                 [RateLimitEvent("allowed"), ResultMessage(result=REPLY)]])
    assert p.limit == "ok"
    p.open_call("rules", {})
    p.step("a")
    assert p.limit == "near"
    with pytest.raises(ProviderUnavailable):
        p.step("b")
    assert p.limit == "reached"
    p.step("c")
    assert p.limit == "ok"
    p.close()
