"""OpenRouter LLM adapter, tested without network: an httpx2
MockTransport captures the outbound request and serves a canned
OpenAI-shaped response, so request shaping and response parsing are
both pinned down."""

import json

import httpx2
import pytest
from openai import DEFAULT_TIMEOUT, APIConnectionError

from engine.adapters.llm_openrouter import (
    API_KEY_ENV_VAR,
    OpenRouterLLM,
    OpenRouterSettings,
)
from engine.ports.llm import LLMTimeoutError
from engine.ports.types import Message, ToolCall, ToolSpec

BASE_URL = "https://example.test/api/v1"


def _completion_json(content="Hello.", tool_calls=None):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        message["content"] = None
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "openrouter/auto",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


@pytest.fixture
def captured():
    return {}


@pytest.fixture
def adapter(monkeypatch, captured):
    def _adapter(response_json=None):
        monkeypatch.setenv(API_KEY_ENV_VAR, "test-key")

        def handler(request: httpx2.Request) -> httpx2.Response:
            captured["request"] = request
            captured["body"] = json.loads(request.content)
            return httpx2.Response(200, json=response_json or _completion_json())

        return OpenRouterLLM(
            OpenRouterSettings(base_url=BASE_URL, model="openrouter/auto"),
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    return _adapter


def test_request_shape(adapter, captured):
    llm = adapter()

    llm.complete(
        [Message(role="system", content="Be terse."),
         Message(role="user", content="Hi.")],
        temperature=0.7,
    )

    request = captured["request"]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == "openrouter/auto"
    assert body["temperature"] == 0.7
    assert body["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hi."},
    ]
    assert "tools" not in body


def test_temperature_defaults_to_zero(adapter, captured):
    """Number-bearing drafting happens at temperature 0 (CLAUDE.md);
    the port's default must be 0, not the provider's default."""
    adapter().complete([Message(role="user", content="Hi.")])

    assert captured["body"]["temperature"] == 0


def test_tools_are_translated_to_openai_shape(adapter, captured):
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}

    adapter().complete(
        [Message(role="user", content="Hi.")],
        tools=[ToolSpec(name="run_sql", description="Run SQL.", input_schema=schema)],
    )

    assert captured["body"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": "Run SQL.",
                "parameters": schema,
            },
        }
    ]


def test_response_parsing_text(adapter):
    response = adapter().complete([Message(role="user", content="Hi.")])

    assert response.content == "Hello."
    assert response.tool_calls == []
    assert response.model == "openrouter/auto"


def test_response_parsing_tool_calls(adapter):
    llm = adapter(
        _completion_json(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "run_sql",
                        "arguments": '{"query": "SELECT 1"}',
                    },
                }
            ]
        )
    )

    response = llm.complete([Message(role="user", content="Count rows.")])

    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "run_sql"
    assert response.tool_calls[0].arguments == {"query": "SELECT 1"}
    assert response.tool_calls[0].id == "call-1"


def test_missing_key_fails_at_first_call_not_construction(monkeypatch):
    """`engine info` must be able to resolve this adapter without
    credentials; only an actual completion needs the key."""
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)

    llm = OpenRouterLLM(OpenRouterSettings(base_url=BASE_URL, model="m"))  # no error

    with pytest.raises(RuntimeError, match=API_KEY_ENV_VAR):
        llm.complete([Message(role="user", content="Hi.")])


def test_usage_is_captured_when_reported(adapter):
    """The eval harness's cost accounting reads LLMResponse.usage;
    the adapter must not discard what the provider reports."""
    with_usage = _completion_json()
    with_usage["usage"] = {
        "prompt_tokens": 120,
        "completion_tokens": 34,
        "total_tokens": 154,
    }

    response = adapter(with_usage).complete(
        [Message(role="user", content="Hi.")]
    )

    assert response.usage is not None
    assert response.usage.prompt_tokens == 120
    assert response.usage.completion_tokens == 34


def test_usage_defaults_to_none_when_absent(adapter):
    response = adapter().complete([Message(role="user", content="Hi.")])
    assert response.usage is None


def test_assistant_tool_calls_are_sent_natively(adapter, captured):
    """The loop transcript replays a prior call in the provider's own
    shape — an assistant message carrying tool_calls, content None
    when it said nothing — never as prose the model could complete."""
    adapter().complete(
        [
            Message(role="user", content="Count rows."),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="call-1", name="run_sql", arguments={"question": "q"})
                ],
            ),
        ]
    )

    sent = captured["body"]["messages"][1]
    assert set(sent) == {"role", "content", "tool_calls"}
    assert sent["role"] == "assistant"
    assert sent["content"] is None
    (call,) = sent["tool_calls"]
    assert call["id"] == "call-1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "run_sql"
    assert json.loads(call["function"]["arguments"]) == {"question": "q"}


def test_tool_results_are_sent_as_tool_messages(adapter, captured):
    adapter().complete(
        [
            Message(role="user", content="Count rows."),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="run_sql", arguments={})],
            ),
            Message(role="tool", tool_call_id="call-1", content='{"rows": 1}'),
        ]
    )

    assert captured["body"]["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"rows": 1}',
    }


def test_a_spoken_tool_call_keeps_its_content(adapter, captured):
    adapter().complete(
        [
            Message(
                role="assistant",
                content="Let me check.",
                tool_calls=[ToolCall(id="call-1", name="run_sql", arguments={})],
            )
        ]
    )

    sent = captured["body"]["messages"][0]
    assert sent["content"] == "Let me check."
    assert sent["tool_calls"][0]["id"] == "call-1"


# --- Timeouts (Migration Readiness) ------------------------------------


def _failing_adapter(monkeypatch, exc_type, **settings):
    """An adapter whose transport raises exc_type on every request, with
    the SDK's own retries off so the failure reaches the engine at once."""
    monkeypatch.setenv(API_KEY_ENV_VAR, "test-key")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("transport failure", request=request)

    return OpenRouterLLM(
        OpenRouterSettings(base_url=BASE_URL, model="m", max_retries=0, **settings),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_a_timeout_surviving_the_sdk_retries_is_the_ports_error(monkeypatch):
    """The eval runner retries a rep on the port's error, never on the
    SDK's: the adapter owns the translation, and names the model."""
    llm = _failing_adapter(monkeypatch, httpx2.ReadTimeout)

    with pytest.raises(LLMTimeoutError, match="m: "):
        llm.complete([Message(role="user", content="Hi.")])


def test_only_timeouts_are_translated(monkeypatch):
    """A refused connection is not a timeout: it propagates as the
    SDK's own error, so nothing upstream mistakes it for one."""
    llm = _failing_adapter(monkeypatch, httpx2.ConnectError)

    with pytest.raises(APIConnectionError):
        llm.complete([Message(role="user", content="Hi.")])


def test_unset_timeouts_keep_the_sdk_defaults(monkeypatch):
    """The client must never receive None for its timeout — the SDK
    reads None as "no timeout at all". Unset means the SDK default
    (read 600 s, connect 5 s) and the SDK's two retries."""
    monkeypatch.setenv(API_KEY_ENV_VAR, "test-key")
    client = OpenRouterLLM(OpenRouterSettings(base_url=BASE_URL, model="m"))._connect()

    assert client.timeout == DEFAULT_TIMEOUT
    assert client.timeout.read == 600 and client.timeout.connect == 5.0
    assert client.max_retries == 2


def test_timeouts_are_set_by_phase(monkeypatch):
    """The failures on record were connect-phase (about 16 s of wall,
    zero completed calls): the two phases are separate knobs, and one
    set alone leaves the other at the SDK default."""
    monkeypatch.setenv(API_KEY_ENV_VAR, "test-key")
    both = OpenRouterLLM(
        OpenRouterSettings(
            base_url=BASE_URL, model="m",
            read_timeout_seconds=30, connect_timeout_seconds=3, max_retries=1,
        )
    )._connect()
    assert both.timeout == httpx2.Timeout(30.0, connect=3.0)
    assert both.max_retries == 1

    read_only = OpenRouterLLM(
        OpenRouterSettings(base_url=BASE_URL, model="m", read_timeout_seconds=30)
    )._connect()
    assert read_only.timeout == httpx2.Timeout(30.0, connect=5.0)

    connect_only = OpenRouterLLM(
        OpenRouterSettings(base_url=BASE_URL, model="m", connect_timeout_seconds=3)
    )._connect()
    assert connect_only.timeout == httpx2.Timeout(600.0, connect=3.0)
