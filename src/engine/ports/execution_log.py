"""ExecutionLogPort — did the target app's components actually run?

Pluggable substrate; a pack may not enable it. Local adapter (Phase 3):
structured log files emitted by real target-app runs (the reference
app's simulation writes them). Real adapter (later phase): Splunk REST
search jobs.

Narrow by design: the port exposes intent-shaped methods only. SPL
templates live in pack config, and the LLM never generates SPL —
capability lives in registered tools, never ad-hoc LLM freedom.
"""

from typing import Any, Protocol

from engine.ports.types import RunStatus, TimeWindow


class ExecutionLogPort(Protocol):
    def did_run(self, component: str, key: str, window: TimeWindow) -> RunStatus: ...

    def recent_errors(
        self, component: str, window: TimeWindow
    ) -> list[dict[str, Any]]: ...
