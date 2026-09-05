"""Databricks FM adapter, tested without network: an httpx2
MockTransport captures the outbound request and serves a canned
OpenAI-shaped response. The twin test at the end feeds one transcript
through this adapter and the OpenRouter one and requires the two
request bodies to be identical — the price of keeping two complete
files instead of a shared base (Brief §3)."""

import json
from pathlib import Path

import httpx2
import pytest
from openai import APIConnectionError, DEFAULT_TIMEOUT

from engine.adapters import llm_openrouter
from engine.adapters.llm_databricks_fm import (
    HOST_ENV_VAR,
    TOKEN_ENV_VAR,
    DatabricksFmLLM,
    DatabricksFmSettings,
    base_url_for,
)
from engine.config.models import PortName
from engine.ports.llm import LLMTimeoutError
from engine.ports.types import Message, ToolCall, ToolSpec
from engine.runtime.registry import default_registry

HOST = "https://example.test"


def _completion_json(content="Hello.", tool_calls=None):
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        message["content"] = None
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "endpoint-1",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


@pytest.fixture
def captured():
    return {}


@pytest.fixture
def adapter(monkeypatch, captured):
    def _adapter(response_json=None, host=HOST, **settings):
        monkeypatch.setenv(HOST_ENV_VAR, host)
        monkeypatch.setenv(TOKEN_ENV_VAR, "dapi-test")

        def handler(request: httpx2.Request) -> httpx2.Response:
            captured["request"] = request
            captured["body"] = json.loads(request.content)
            return httpx2.Response(200, json=response_json or _completion_json())

        return DatabricksFmLLM(
            DatabricksFmSettings(model="endpoint-1", **settings),
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    return _adapter


def test_request_goes_to_the_serving_endpoints_path(adapter, captured):
    adapter().complete(
        [Message(role="system", content="Be terse."), Message(role="user", content="Hi.")],
        temperature=0.7,
    )

    request = captured["request"]
    assert str(request.url) == f"{HOST}/serving-endpoints/chat/completions"
    assert request.headers["authorization"] == "Bearer dapi-test"
    body = captured["body"]
    assert body["model"] == "endpoint-1"
    assert body["temperature"] == 0.7
    assert body["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hi."},
    ]
    assert "tools" not in body


@pytest.mark.parametrize(
    "host",
    ["example.test", "https://example.test/", "example.test/"],
)
def test_host_is_read_with_or_without_scheme_and_slash(adapter, captured, host):
    """What a person pastes into DATABRICKS_HOST varies; every spelling
    must land on the same URL, or the first failure is a 404 that reads
    like a wrong endpoint name."""
    adapter(host=host).complete([Message(role="user", content="Hi.")])
    assert str(captured["request"].url) == f"{HOST}/serving-endpoints/chat/completions"


def test_base_url_for():
    assert base_url_for("adb-1.azuredatabricks.net", "/serving-endpoints") == (
        "https://adb-1.azuredatabricks.net/serving-endpoints"
    )


def test_temperature_defaults_to_zero(adapter, captured):
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
            "function": {"name": "run_sql", "description": "Run SQL.", "parameters": schema},
        }
    ]


def test_response_parsing_tool_calls(adapter):
    llm = adapter(
        _completion_json(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"query": "SELECT 1"}'},
                }
            ]
        )
    )
    response = llm.complete([Message(role="user", content="Count rows.")])
    assert response.content == ""
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("call-1", "run_sql", {"query": "SELECT 1"})
    ]
    assert response.model == "endpoint-1"


def test_usage_is_captured_when_reported(adapter):
    with_usage = _completion_json()
    with_usage["usage"] = {"prompt_tokens": 120, "completion_tokens": 34, "total_tokens": 154}
    response = adapter(with_usage).complete([Message(role="user", content="Hi.")])
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (120, 34)


@pytest.mark.parametrize("missing", [HOST_ENV_VAR, TOKEN_ENV_VAR])
def test_missing_env_fails_at_first_call_not_construction(monkeypatch, missing):
    """`engine info` must resolve this adapter without credentials; the
    error names the variable so a chat-side debugger knows what to set."""
    monkeypatch.setenv(HOST_ENV_VAR, HOST)
    monkeypatch.setenv(TOKEN_ENV_VAR, "dapi-test")
    monkeypatch.delenv(missing)

    llm = DatabricksFmLLM(DatabricksFmSettings(model="endpoint-1"))  # no error

    with pytest.raises(RuntimeError, match=missing):
        llm.complete([Message(role="user", content="Hi.")])


def _failing_adapter(monkeypatch, exc_type):
    monkeypatch.setenv(HOST_ENV_VAR, HOST)
    monkeypatch.setenv(TOKEN_ENV_VAR, "dapi-test")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("transport failure", request=request)

    return DatabricksFmLLM(
        DatabricksFmSettings(model="endpoint-1", max_retries=0),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_a_timeout_surviving_the_sdk_retries_is_the_ports_error(monkeypatch):
    llm = _failing_adapter(monkeypatch, httpx2.ReadTimeout)
    with pytest.raises(LLMTimeoutError, match="endpoint-1: "):
        llm.complete([Message(role="user", content="Hi.")])


def test_only_timeouts_are_translated(monkeypatch):
    llm = _failing_adapter(monkeypatch, httpx2.ConnectError)
    with pytest.raises(APIConnectionError):
        llm.complete([Message(role="user", content="Hi.")])


def test_unset_timeouts_keep_the_sdk_defaults(monkeypatch):
    monkeypatch.setenv(HOST_ENV_VAR, HOST)
    monkeypatch.setenv(TOKEN_ENV_VAR, "dapi-test")
    client = DatabricksFmLLM(DatabricksFmSettings(model="endpoint-1"))._connect()
    assert client.timeout == DEFAULT_TIMEOUT
    assert client.max_retries == 2

    tuned = DatabricksFmLLM(
        DatabricksFmSettings(
            model="endpoint-1", read_timeout_seconds=30, connect_timeout_seconds=3
        )
    )._connect()
    assert tuned.timeout == httpx2.Timeout(30.0, connect=3.0)


def test_the_pack_key_resolves(monkeypatch):
    """Config over code: a pack switches providers by naming the key."""
    monkeypatch.delenv(HOST_ENV_VAR, raising=False)
    llm = default_registry().create(
        PortName.LLM, "databricks_fm", {"model": "endpoint-1"}, Path(".")
    )
    assert isinstance(llm, DatabricksFmLLM)
    assert llm.settings.model == "endpoint-1"


# --- The twin test --------------------------------------------------------


TRANSCRIPT = [
    Message(role="system", content="Be terse."),
    Message(role="user", content="Count rows."),
    Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call-1", name="run_sql", arguments={"question": "q"})],
    ),
    Message(role="tool", content='{"rows": 3}', tool_call_id="call-1"),
    Message(role="assistant", content="Three."),
    Message(role="user", content="And last week?"),
]
TOOLS = [
    ToolSpec(
        name="run_sql",
        description="Run SQL.",
        input_schema={"type": "object", "properties": {"question": {"type": "string"}}},
    ),
    ToolSpec(name="give_answer", description="Answer.", input_schema={"type": "object"}),
]


def _capture(build):
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx2.Response(200, json=_completion_json())

    llm = build(httpx2.Client(transport=httpx2.MockTransport(handler)))
    llm.complete(TRANSCRIPT, tools=TOOLS, temperature=0.0)
    return captured


def test_twin_adapters_shape_one_transcript_identically(monkeypatch):
    """Two complete files, one contract: the router loop's transcript —
    native tool_calls, one role="tool" message per call by id, no empty
    tool_calls on plain messages — must reach either provider in the
    same shape. Only the model name may differ."""
    monkeypatch.setenv(llm_openrouter.API_KEY_ENV_VAR, "or-key")
    monkeypatch.setenv(HOST_ENV_VAR, HOST)
    monkeypatch.setenv(TOKEN_ENV_VAR, "dapi-test")

    via_openrouter = _capture(
        lambda client: llm_openrouter.OpenRouterLLM(
            llm_openrouter.OpenRouterSettings(base_url=HOST + "/api/v1", model="a"),
            http_client=client,
        )
    )
    via_databricks = _capture(
        lambda client: DatabricksFmLLM(
            DatabricksFmSettings(model="b"), http_client=client
        )
    )

    assert via_openrouter["body"].pop("model") == "a"
    assert via_databricks["body"].pop("model") == "b"
    assert via_openrouter["body"] == via_databricks["body"]
    assert via_openrouter["headers"]["authorization"].startswith("Bearer ")
    assert via_databricks["headers"]["authorization"].startswith("Bearer ")
