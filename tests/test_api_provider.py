import os
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from callcoach.api_provider import KEEP_PAIRS, MAX_PAIRS, ApiProvider, map_error, thinking_for
from callcoach.claude_provider import SCRUB_NAMES, SCRUB_PREFIXES
from callcoach.providers import ProviderAuth, ProviderIncomplete, ProviderUnavailable, Usage


def reply(text, stop_reason="end_turn", **usage):
    u = dict(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    u.update(usage)
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="..."),
                                    SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(**u),
                           stop_reason=stop_reason)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.timeouts = []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def with_options(self, timeout):
        self.timeouts.append(timeout)
        return self

    def create(self, **kw):
        self.calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def provider(replies):
    client = FakeClient(replies)
    return ApiProvider(None, step_model="claude-haiku-5-5", strong_model="claude-sonnet-5-5", client=client,
                       environ={}), client


def test_steps_keep_one_cached_conversation():
    p, c = provider([reply('{"card": null, "goals": []}', cache_read_input_tokens=900), reply("{}")])
    p.open_call("rules", {"type": "object"})
    first = p.step("line 1")
    p.step("line 2")
    assert first.data == '{"card": null, "goals": []}' and first.usage == Usage(10, 5, 900, 0, 1)
    second = c.calls[1]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert second["cache_control"] == {"type": "ephemeral"}
    assert second["thinking"] == {"type": "disabled"} and second["model"] == "claude-haiku-5-5"
    assert second["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}


def test_a_failed_step_leaves_the_conversation_clean():
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    p, c = provider([err, reply("{}")])
    p.open_call("rules", {})
    with pytest.raises(ProviderUnavailable):
        p.step("a")
    p.step("b")
    assert [m["content"] for m in c.calls[1]["messages"]] == ["b"]


def test_prepare_and_summary_use_the_strong_model_with_fallbacks():
    p, c = provider([reply('{"goals": [], "questions": [], "glossary": []}'), reply("## סיכום")])
    p.prepare("prep rules", "notes", {"type": "object"})
    summary = p.summarize("summary rules", "transcript")
    assert [x["model"] for x in c.calls] == ["claude-sonnet-5-5", "claude-sonnet-5-5"]
    assert c.calls[1]["thinking"] == {"type": "adaptive"} and "format" not in c.calls[1]["output_config"]
    assert all(x["fallbacks"] == "default" and x["betas"] == ["server-side-fallback-2026-07-01"] for x in c.calls)
    assert summary.data == "## סיכום"


def test_steps_do_not_ask_for_fallbacks():
    p, c = provider([reply("{}")])
    p.open_call("rules", {})
    p.step("x")
    assert "fallbacks" not in c.calls[0] and "betas" not in c.calls[0]


def test_a_refusal_gives_empty_text():
    p, c = provider([reply("partial", stop_reason="refusal")])
    p.open_call("rules", {})
    assert p.step("x").data == ""


def test_thinking_follows_the_model():
    assert thinking_for("claude-haiku-5-5") == {"type": "disabled"}
    assert thinking_for("claude-sonnet-5-5") == {"type": "adaptive"}


@pytest.mark.parametrize("cls, expected", [
    (anthropic.AuthenticationError, ProviderAuth),
    (anthropic.PermissionDeniedError, ProviderAuth),
    (anthropic.BadRequestError, ProviderUnavailable),
    (anthropic.RateLimitError, ProviderUnavailable),
    (anthropic.InternalServerError, ProviderUnavailable),
    (anthropic.APIConnectionError, ProviderUnavailable),
    (anthropic.APITimeoutError, ProviderUnavailable),
])
def test_errors_map_to_provider_errors(cls, expected):
    assert isinstance(map_error(cls.__new__(cls)), expected)


def test_other_errors_pass_through():
    e = ValueError("bug")
    assert map_error(e) is e


MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
           "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn", "stop_sequence": None,
           "usage": {"input_tokens": 3, "output_tokens": 1}}


def test_the_real_client_ignores_an_inherited_proxy_and_its_headers(monkeypatch):
    for key in [k for k in os.environ if k.startswith(SCRUB_PREFIXES) or k in SCRUB_NAMES]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Proxy-Tag: inherited")
    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(200, json=MESSAGE)

    p = ApiProvider("test-key", step_model="claude-haiku-5-5", strong_model="claude-sonnet-5-5")
    assert str(p.client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert "ANTHROPIC_BASE_URL" not in os.environ and "ANTHROPIC_CUSTOM_HEADERS" not in os.environ
    p.client = p.client.with_options(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    assert p.check().data == "OK"
    [request] = sent
    assert request.url.host == "api.anthropic.com" and "x-proxy-tag" not in request.headers


def bad_request(message="prompt is too long"):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(message, response=httpx2.Response(400, request=request),
                                     body={"type": "error", "error": {"type": "invalid_request_error",
                                                                      "message": message}})


def test_the_conversation_keeps_only_the_recent_pairs():
    steps = MAX_PAIRS + 2
    p, c = provider([reply(f"{{}} {i}") for i in range(steps)])
    p.open_call("rules", {})
    for i in range(steps):
        p.step(f"line {i}")
    sizes = [len(call["messages"]) for call in c.calls]
    assert sizes[:MAX_PAIRS + 1] == [2 * i + 1 for i in range(MAX_PAIRS + 1)]
    assert sizes[MAX_PAIRS + 1] == 2 * KEEP_PAIRS + 1 and max(sizes) == 2 * MAX_PAIRS + 1
    kept = c.calls[-1]["messages"]
    assert kept[0] == {"role": "user", "content": f"line {steps - 1 - KEEP_PAIRS}"}
    assert [m["role"] for m in kept] == ["user", "assistant"] * KEEP_PAIRS + ["user"]
    assert all(call["system"][0]["text"] == "rules" for call in c.calls)


def test_an_invalid_request_resets_the_conversation_and_retries_once():
    p, c = provider([reply("{} 1"), reply("{} 2"), bad_request(), reply("{} 3")])
    p.open_call("rules", {})
    p.step("a")
    p.step("b")
    assert p.step("c").data == "{} 3"
    assert [m["content"] for m in c.calls[2]["messages"]] == ["a", "{} 1", "b", "{} 2", "c"]
    assert [m["content"] for m in c.calls[3]["messages"]] == ["c"]
    assert p.messages == [{"role": "user", "content": "c"}, {"role": "assistant", "content": "{} 3"}]
    assert p.take_notes() == [("conversation_reset", {"message": "400: prompt is too long"})]


def test_a_second_invalid_request_is_unavailable_and_leaves_no_turn_behind():
    p, c = provider([reply("{} 1"), bad_request(), bad_request(), reply("{} 2")])
    p.open_call("rules", {})
    p.step("a")
    with pytest.raises(ProviderUnavailable, match="400"):
        p.step("b")
    assert p.messages == []
    assert p.step("c").data == "{} 2" and [m["content"] for m in c.calls[3]["messages"]] == ["c"]


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def web_reply(content, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=10,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, usage=usage, stop_reason=stop_reason)


def test_an_explanation_is_one_request_to_the_live_model():
    p, c = provider([reply("הסבר קצר")])
    p.open_call("rules", {})
    result = p.explain("explain rules", "term: Okta")
    assert result.data == "הסבר קצר" and result.model == "claude-haiku-5-5" and p.messages == []
    [call] = c.calls
    assert call["model"] == "claude-haiku-5-5" and "tools" not in call and c.timeouts == [15.0]


def test_a_translation_is_one_request_to_the_live_model():
    p, c = provider([reply('{"lines": []}')])
    p.open_call("rules", {})
    result = p.translate("translate rules", "## Translate\n1: hello")
    assert result.data == '{"lines": []}' and result.model == "claude-haiku-5-5" and p.messages == []
    [call] = c.calls
    assert call["model"] == "claude-haiku-5-5" and "tools" not in call and c.timeouts == [15.0]


def test_a_screenshot_is_one_request_with_the_image_to_the_live_model():
    p, c = provider([reply('{"screen": "שקף"}')])
    p.open_call("rules", {})
    result = p.look("screen rules", "## The call", b"png bytes", "image/png")
    assert result.data == '{"screen": "שקף"}' and result.model == "claude-haiku-5-5" and p.messages == []
    [call] = c.calls
    image, text = call["messages"][0]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": "cG5nIGJ5dGVz"}
    assert text == {"type": "text", "text": "## The call"} and call["model"] == "claude-haiku-5-5"


def test_a_recap_is_one_request_to_the_strong_model():
    p, c = provider([reply("- שורה")])
    p.open_call("rules", {})
    result = p.recap("recap rules", "## Transcript")
    assert result.data == "- שורה" and result.model == "claude-sonnet-5-5" and p.messages == []
    [call] = c.calls
    assert call["model"] == "claude-sonnet-5-5" and "tools" not in call and c.timeouts == [40.0]


def test_the_research_uses_web_search_and_web_fetch_and_goes_on_after_a_pause():
    paused = [block("text", text="I will look them up."), block("server_tool_use", id="s1", name="web_search"),
              block("web_search_tool_result", tool_use_id="s1", content=[])]
    done = [block("server_tool_use", id="s2", name="web_fetch"), block("web_fetch_tool_result", tool_use_id="s2"),
            block("text", text="---\ntitle: שיחה עם Acme\n---\n## רקע\nחברה "), block("text", text="לדוגמה.")]
    p, c = provider([web_reply(paused, "pause_turn"), web_reply(done)])
    result = p.research("research rules", "request")
    assert result.data == "---\ntitle: שיחה עם Acme\n---\n## רקע\nחברה לדוגמה."
    assert result.usage == Usage(200, 100, 20, 0, 2) and result.model == "claude-sonnet-5-5"
    first, second = c.calls
    assert first["tools"] == [{"type": "web_search_20260209", "name": "web_search"},
                              {"type": "web_fetch_20260209", "name": "web_fetch"}]
    assert first["model"] == "claude-sonnet-5-5" and first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default" and first["max_tokens"] == 16000 and c.timeouts == [600.0, 600.0]
    assert first["messages"] == [{"role": "user", "content": "request"}]
    assert second["messages"] == [{"role": "user", "content": "request"}, {"role": "assistant", "content": paused}]


def test_a_research_that_keeps_pausing_is_incomplete():
    from callcoach.api_provider import MAX_CONTINUATIONS

    p, c = provider([web_reply([block("text", text="...")], "pause_turn")] * (MAX_CONTINUATIONS + 1))
    with pytest.raises(ProviderIncomplete, match="continuations"):
        p.research("research rules", "request")
    assert len(c.calls) == MAX_CONTINUATIONS + 1


def test_the_brief_is_written_with_no_tools():
    p, c = provider([reply("---\ntitle: שיחה עם Acme\n---\n## רקע")])
    result = p.brief("brief rules", "request and research notes")
    assert result.data == "---\ntitle: שיחה עם Acme\n---\n## רקע" and result.model == "claude-sonnet-5-5"
    [call] = c.calls
    assert "tools" not in call and call["model"] == "claude-sonnet-5-5" and call["output_config"] == {"effort": "low"}
    assert call["messages"] == [{"role": "user", "content": "request and research notes"}]


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "stop_sequence", "model_context_window_exceeded"])
def test_an_answer_that_did_not_end_is_never_a_brief_or_research(stop_reason):
    p, c = provider([reply("---\ntitle: x\n---\n## רקע\nחצי", stop_reason=stop_reason)])
    with pytest.raises(ProviderIncomplete, match=stop_reason):
        p.brief("brief rules", "request")
    p, c = provider([web_reply([block("text", text="## People\nhalf")], stop_reason)])
    with pytest.raises(ProviderIncomplete, match=stop_reason):
        p.research("research rules", "request")


def test_the_followup_is_written_by_the_strong_model_with_no_tools():
    p, c = provider([reply("Subject: Thanks\n\nHi Dana")])
    result = p.followup("followup rules", "transcript")
    assert result.data == "Subject: Thanks\n\nHi Dana" and result.model == "claude-sonnet-5-5"
    [call] = c.calls
    assert "tools" not in call and call["model"] == "claude-sonnet-5-5" and call["system"][0]["text"] == "followup rules"
    assert call["messages"] == [{"role": "user", "content": "transcript"}]


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_a_followup_that_did_not_end_is_incomplete(stop_reason):
    p, c = provider([reply("Subject: Thanks\n\nHi", stop_reason=stop_reason)])
    with pytest.raises(ProviderIncomplete, match=stop_reason):
        p.followup("followup rules", "transcript")


def test_steps_and_summaries_have_no_tools():
    p, c = provider([reply("{}"), reply("## סיכום")])
    p.open_call("rules", {})
    p.step("x")
    p.summarize("summary rules", "transcript")
    assert all("tools" not in call for call in c.calls)
