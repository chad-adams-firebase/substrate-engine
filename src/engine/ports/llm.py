"""LLMPort — completion calls against a language model.

Local adapter: OpenRouter. Real adapter (later phase): Databricks FM
serving. Both are OpenAI-compatible, which is why this port takes a
messages list rather than the Brief's minimum single-prompt signature
(deviation flagged in the Phase 1 plan): the harness needs system +
history turns, and both real backends are messages-shaped.

A deterministic scripted stub exists under tests/ — it is pytest
plumbing, not a development mode, and is deliberately not part of the
engine package.
"""

from typing import Protocol

from engine.ports.types import LLMResponse, Message, ToolSpec


class LLMPort(Protocol):
    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...
