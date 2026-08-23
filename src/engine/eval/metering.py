"""MeteringLLM: a port-typed decorator recording cost and latency.

Wraps the already-built LLM adapter (after build(), before
build_tools) so every router/drafter/judge call in an eval run is
counted — no adapter import, no port change beyond LLMResponse.usage.
The runner resets it per turn and snapshots stats() into the report.
"""

import time

from engine.eval.models import LlmStats
from engine.ports.llm import LLMPort
from engine.ports.types import LLMResponse, Message, ToolSpec


class MeteringLLM:
    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self._stats = LlmStats()

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        started = time.perf_counter()
        response = self._inner.complete(
            messages, tools=tools, temperature=temperature
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._stats.calls += 1
        self._stats.latencies_ms.append(elapsed_ms)
        if response.usage is not None:
            self._stats.prompt_tokens += response.usage.prompt_tokens
            self._stats.completion_tokens += response.usage.completion_tokens
        return response

    def reset(self) -> None:
        self._stats = LlmStats()

    def stats(self) -> LlmStats:
        return self._stats.model_copy(deep=True)
