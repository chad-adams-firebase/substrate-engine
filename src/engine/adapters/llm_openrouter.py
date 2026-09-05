"""OpenRouter adapter for LLMPort.

OpenRouter is OpenAI-compatible, so this uses the openai client with a
different base URL. The Databricks FM serving adapter
(llm_databricks_fm.py) is this file's complete sibling — the same
shaping with a different base URL and auth — kept as two whole files
on purpose (Brief §3: at work a complete file is what gets debugged);
tests/test_adapter_llm_databricks_fm.py pins the two request shapes
identical so the duplication cannot drift.

The API key comes from the OPENROUTER_API_KEY environment variable,
never from pack config: secrets are env-only. A missing key is not an
error until the first completion call, so that `engine info` can
resolve and report this adapter without credentials.

The router loop's transcript is sent natively: an assistant message
that requested tools carries them as tool_calls, and a role="tool"
message answers one call by id — the same shape the provider returns
the calls in, so the model never sees a prose rendering of them.

Timeouts and retries. The SDK's defaults are connect 5 s / read 600 s
with two retries of its own (connect failures, 429, 5xx, timeouts). A
timeout that survives those retries is translated into the port's
LLMTimeoutError — the one error a caller may act on; everything else
propagates raw. The two phases are separate settings because the
failures on record were connect-phase: about 16 s of wall with zero
completed calls (the 5 s connect budget, three attempts), not a slow
read — one all-phase number would make every dead connect cost three
times the read value. Unset settings keep the SDK defaults; the client
is never given None, which the SDK reads as "no timeout at all".
"""

import json
import os

import httpx2
from openai import DEFAULT_TIMEOUT, NOT_GIVEN, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict

from engine.ports.llm import LLMTimeoutError
from engine.ports.types import LLMResponse, Message, TokenUsage, ToolCall, ToolSpec

API_KEY_ENV_VAR = "OPENROUTER_API_KEY"


class OpenRouterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://openrouter.ai/api/v1"
    model: str
    # None keeps the SDK default for that phase (read 600 s, connect
    # 5 s). The read value also covers write and pool.
    read_timeout_seconds: float | None = None
    connect_timeout_seconds: float | None = None
    # The SDK's own retries (connect failures, 429, 5xx, timeouts)
    # before an error reaches the engine. Tests set 0.
    max_retries: int = 2


def _timeout(settings: OpenRouterSettings):
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


class OpenRouterLLM:
    def __init__(
        self,
        settings: OpenRouterSettings,
        http_client: httpx2.Client | None = None,
    ) -> None:
        """http_client is a test seam: pytest passes an httpx2 client with
        a MockTransport so request shaping is testable without network.
        (httpx2 is the openai SDK's HTTP stack — its fork of httpx.)"""
        self._settings = settings
        self._api_key = os.environ.get(API_KEY_ENV_VAR)
        self._http_client = http_client
        self._client: OpenAI | None = None

    @property
    def settings(self) -> OpenRouterSettings:
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
            if not self._api_key:
                raise RuntimeError(
                    f"OpenRouter adapter has no API key: set the "
                    f"{API_KEY_ENV_VAR} environment variable."
                )
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._settings.base_url,
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
        the provider's own response shape, since some OpenRouter routes
        reject an empty text block. A plain message stays the two-key
        dict: no empty tool_calls list leaks onto it."""
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
