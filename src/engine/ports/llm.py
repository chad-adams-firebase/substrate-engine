"""LLMPort — completion calls against a language model.

Local adapter: OpenRouter. Work adapter: Databricks FM serving. Both
are OpenAI-compatible, which is why this port takes a messages list
rather than the Brief's minimum single-prompt signature (deviation
flagged in the Phase 1 plan): the harness needs system + history
turns, and both backends are messages-shaped.

A deterministic scripted stub exists under tests/ — it is pytest
plumbing, not a development mode, and is deliberately not part of the
engine package.

LLMTimeoutError is the port's one error type. An adapter raises it
when its SDK reports a timeout that survived the SDK's own retries;
every other provider failure propagates raw. It exists for exactly
one caller: the eval runner retries a rep once on it (Migration
Readiness), because a provider brownout is not an engine result. A
timeout raised inside a tool (run_sql's NL->SQL call) is caught by the
tool registry like any tool failure and never reaches that seam — by
design: the router already reads tool errors and may take its licensed
retry.
"""

from typing import Protocol

from engine.ports.types import LLMResponse, Message, ToolSpec


class LLMTimeoutError(Exception):
    """The provider did not answer within the adapter's timeout, after
    the SDK's own retries. The message names the model and the SDK's
    text so a report line reads as a provider event, not an engine one."""


class LLMPort(Protocol):
    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...
