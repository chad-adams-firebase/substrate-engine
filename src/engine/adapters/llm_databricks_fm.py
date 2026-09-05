"""Databricks Foundation Model serving adapter for LLMPort.

The FM serving endpoints are OpenAI-compatible, so this uses the openai
client against `<workspace host>/serving-endpoints` with the endpoint
name as the model. It is the complete sibling of llm_openrouter.py —
the same shaping with a different base URL and auth — kept as a whole
file on purpose (Brief §3: at work a complete file is what gets
debugged); tests/test_adapter_llm_databricks_fm.py pins the two
request shapes identical so the duplication cannot drift.

Configuration is split by sensitivity. The pack config carries only
the endpoint name (`model`) and knobs; the workspace host and the
token come from the DATABRICKS_HOST and DATABRICKS_TOKEN environment
variables, never from a file in the repo (the host is an enterprise
name — the de-identification law). Both are checked lazily at the
first completion, so `engine info` resolves this adapter without
credentials. A host given without a scheme is read as https.

Timeouts and retries are as in the OpenRouter adapter: the SDK's
defaults (connect 5 s, read 600 s, two retries of its own) unless set;
a timeout that survives the SDK's retries becomes the port's
LLMTimeoutError; everything else propagates raw.

The FM API returns at most one tool call per response (no parallel
calls); the router loop reads a one-element list without noticing.

Failure modes, for a work-side debugging session (file: this one;
function: DatabricksFmLLM.complete unless named). Each row is an
observable, what it means, and the question to put to the assistant:

  RuntimeError naming DATABRICKS_HOST / DATABRICKS_TOKEN
      the variable is unset in the shell that ran `engine`; set it in
      that same shell (Windows: `set NAME=value`, or a user env var)
      and re-run — "which shell exported it, and does `engine info`
      see the same environment?"
  openai.AuthenticationError (HTTP 401)
      the token is expired, revoked, or for another workspace —
      "when was this PAT created, and does the host match the workspace
      that issued it?"
  openai.PermissionDeniedError (HTTP 403)
      the token's user has no CAN QUERY on the endpoint — "who owns the
      endpoint, and can they grant CAN QUERY to this user?"
  openai.NotFoundError (HTTP 404)
      the endpoint name in pack config does not exist in this
      workspace, or base_path is wrong — "what does the Serving page
      list, exactly, for this endpoint's name?"
  openai.BadRequestError (HTTP 400) mentioning tools / functions
      the endpoint's model does not support function calling; the
      engine cannot run without it — "which endpoints in this
      workspace list function calling as supported?"
  openai.RateLimitError (HTTP 429)
      the endpoint's or the workspace's rate limit; the SDK already
      retried twice with backoff — "what is the endpoint's rate limit,
      and is anyone else querying it now?"
  LLMTimeoutError
      no answer within the timeouts after the SDK's retries; about 16 s
      of wall with zero calls completed means the connect phase (host
      unreachable from this network), a long wait means a slow model —
      "does the smoke script (scripts/fm_smoke.py) answer right now?"
  ssl / certificate errors
      a TLS-intercepting proxy; the openai client's httpx2 stack uses
      the OS certificate store (truststore), so the corporate root must
      be installed in Windows — "is the corporate root CA in the Windows
      certificate store, and does `curl` to the host succeed?"
"""

import json
import os

import httpx2
from openai import DEFAULT_TIMEOUT, NOT_GIVEN, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict

from engine.ports.llm import LLMTimeoutError
from engine.ports.types import LLMResponse, Message, TokenUsage, ToolCall, ToolSpec

HOST_ENV_VAR = "DATABRICKS_HOST"
TOKEN_ENV_VAR = "DATABRICKS_TOKEN"


class DatabricksFmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The serving endpoint's name as the Serving page lists it.
    model: str
    base_path: str = "/serving-endpoints"
    # None keeps the SDK default for that phase (read 600 s, connect
    # 5 s). The read value also covers write and pool.
    read_timeout_seconds: float | None = None
    connect_timeout_seconds: float | None = None
    # The SDK's own retries (connect failures, 429, 5xx, timeouts)
    # before an error reaches the engine. Tests set 0.
    max_retries: int = 2


def _timeout(settings: DatabricksFmSettings):
    """The SDK's timeout argument: NOT_GIVEN when nothing is set (the
    SDK default applies), else an httpx2.Timeout filling the unset
    phase from that same default."""
    read = settings.read_timeout_seconds
    connect = settings.connect_timeout_seconds
    if read is None and connect is None:
        return NOT_GIVEN
    return httpx2.Timeout(
        read if read is not None else DEFAULT_TIMEOUT.read,
        connect=connect if connect is not None else DEFAULT_TIMEOUT.connect,
    )


def base_url_for(host: str, base_path: str) -> str:
    """`https://<host>/serving-endpoints` from a host given with or
    without a scheme or trailing slash."""
    if "://" not in host:
        host = "https://" + host
    return host.rstrip("/") + base_path


class DatabricksFmLLM:
    def __init__(
        self,
        settings: DatabricksFmSettings,
        http_client: httpx2.Client | None = None,
    ) -> None:
        """http_client is a test seam: pytest passes an httpx2 client with
        a MockTransport so request shaping is testable without network.
        (httpx2 is the openai SDK's HTTP stack — its fork of httpx.)"""
        self._settings = settings
        self._host = os.environ.get(HOST_ENV_VAR)
        self._token = os.environ.get(TOKEN_ENV_VAR)
        self._http_client = http_client
        self._client: OpenAI | None = None

    @property
    def settings(self) -> DatabricksFmSettings:
        return self._settings

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        try:
            response = self._connect().chat.completions.create(
                model=self._settings.model,
                messages=[self._to_openai_message(m) for m in messages],
                tools=[self._to_openai_tool(t) for t in tools] if tools else NOT_GIVEN,
                temperature=temperature,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                f"{self._settings.model}: {exc} (after {self._settings.max_retries} "
                f"SDK retries)"
            ) from exc
        choice = response.choices[0].message
        return LLMResponse(
            content=choice.content or "",
            tool_calls=[
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=json.loads(call.function.arguments),
                )
                for call in (choice.tool_calls or [])
            ],
            model=response.model,
            usage=(
                TokenUsage(
                    prompt_tokens=response.usage.prompt_tokens or 0,
                    completion_tokens=response.usage.completion_tokens or 0,
                )
                if response.usage is not None
                else None
            ),
        )

    def _connect(self) -> OpenAI:
        if self._client is None:
            if not self._host:
                raise RuntimeError(
                    f"Databricks FM adapter has no workspace host: set the "
                    f"{HOST_ENV_VAR} environment variable "
                    f"(https://<workspace host>)."
                )
            if not self._token:
                raise RuntimeError(
                    f"Databricks FM adapter has no token: set the "
                    f"{TOKEN_ENV_VAR} environment variable."
                )
            self._client = OpenAI(
                api_key=self._token,
                base_url=base_url_for(self._host, self._settings.base_path),
                http_client=self._http_client,
                timeout=_timeout(self._settings),
                max_retries=self._settings.max_retries,
            )
        return self._client

    @staticmethod
    def _to_openai_message(message: Message) -> dict:
        """The wire shape of one message. A tool message answers a call
        by id; an assistant message that requested tools carries them
        natively, with content None when it said nothing — mirroring
        the provider's own response shape. A plain message stays the
        two-key dict: no empty tool_calls list leaks onto it."""
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        if message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        return {"role": message.role, "content": message.content}

    @staticmethod
    def _to_openai_tool(tool: ToolSpec) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
