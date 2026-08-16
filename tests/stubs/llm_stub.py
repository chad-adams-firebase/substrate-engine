"""Deterministic scripted LLMPort stub — pytest plumbing ONLY.

Lives under tests/, not in the engine package, on purpose: the Brief
is explicit that this is test plumbing, not a development mode. Tests
that need it in a DI container register it into an AdapterRegistry
themselves (which also proves adapter registration is open to tests
without touching engine code).
"""

from engine.ports.types import LLMResponse, Message, ToolSpec


class ScriptedLLM:
    """Returns pre-scripted responses in order and records every call
    for assertions. Deterministic by construction: no randomness, no
    network, no state beyond the script position."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self._position = 0
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "tools": tools, "temperature": temperature}
        )
        if self._position >= len(self._responses):
            raise AssertionError(
                f"ScriptedLLM exhausted: {len(self._responses)} responses "
                f"scripted, call #{self._position + 1} made."
            )
        response = self._responses[self._position]
        self._position += 1
        return response
