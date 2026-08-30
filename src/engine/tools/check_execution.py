"""check_execution — did a target-app component actually run, and what
errored, answered from the execution log through intent-shaped port
methods. The component→query mapping is pack config; nothing here (or
anywhere) lets the LLM write a log query.
"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from engine.config.models import CheckExecutionSettings, SubstrateName, ToolName
from engine.ports.execution_log import ExecutionLogError, ExecutionLogPort
from engine.ports.types import LogEvent, TimeWindow
from engine.tools.base import Tool
from engine.tools.coverage import CoverageWindow
from engine.tools.envelope import (
    CheckExecutionEvidence,
    CheckExecutionOutput,
    JsonValue,
    ToolInvocation,
)


class CheckExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["did_run", "recent_errors"] = "did_run"
    component: str
    # Narrows did_run to one unit of work (an invoice id, a file name)
    # where the component's template configures a key field.
    key: str = ""
    window_start: datetime
    window_end: datetime

    @model_validator(mode="after")
    def _ordered_window(self) -> "CheckExecutionInput":
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        return self


def _event_row(event: LogEvent) -> dict[str, JsonValue]:
    return {
        "ts": event.ts.isoformat(),
        "level": event.level,
        "logger": event.logger,
        "event": event.event,
        **event.fields,
    }


class CheckExecution(Tool):
    name = ToolName.CHECK_EXECUTION
    description = (
        "Check the application's execution log: did a named component "
        "run in a time window (mode 'did_run', optionally narrowed by a "
        "key such as an invoice id), or what error-level events did it "
        "emit (mode 'recent_errors'). Timestamps are ISO 8601 with "
        "timezone. Windows must fall inside the pack's data coverage; "
        "an out-of-range window returns an error naming the coverage."
    )
    input_model = CheckExecutionInput

    def __init__(
        self,
        log: ExecutionLogPort,
        settings: CheckExecutionSettings,
        coverage: CoverageWindow | None = None,
    ) -> None:
        self._log = log
        self._settings = settings
        self._coverage = coverage

    def run(self, params: CheckExecutionInput) -> ToolInvocation:
        # A window entirely outside data coverage is a hallucinated
        # date (carryback #3a: an invented 2023 window returned an
        # honest ran:false that would have VERIFIED a wrong "no").
        # Steering error, recoverable: the router re-asks in range.
        if self._coverage is not None:
            grace = timedelta(days=self._settings.coverage_grace_days)
            if (
                params.window_end.date() < self._coverage.start - grace
                or params.window_start.date() > self._coverage.end + grace
            ):
                return self.fail(
                    params,
                    (
                        f"The window [{params.window_start.date()} .. "
                        f"{params.window_end.date()}] is entirely outside "
                        f"the data coverage [{self._coverage.start} .. "
                        f"{self._coverage.end}]. Re-ask with a window "
                        "inside coverage; relative dates anchor to the "
                        "coverage end, not today."
                    ),
                )
        window = TimeWindow(start=params.window_start, end=params.window_end)
        try:
            if params.mode == "did_run":
                status = self._log.did_run(params.component, params.key, window)
                # Receipts move to evidence; the LLM-visible output
                # keeps the answer and the true count.
                return self.ok(
                    params,
                    CheckExecutionOutput(
                        run_status=status.model_copy(update={"matched_lines": []})
                    ),
                    evidence=CheckExecutionEvidence(
                        lines=status.matched_lines,
                        truncated=status.count > len(status.matched_lines),
                    ),
                    substrates_read=[SubstrateName.APPLICATION_LOGS],
                )
            events = self._log.recent_errors(params.component, window)
        except ExecutionLogError as exc:
            return self.fail(params, str(exc))
        kept = events[: self._settings.max_errors]
        return self.ok(
            params,
            CheckExecutionOutput(
                errors=[_event_row(event) for event in kept],
                error_count=len(events),
            ),
            evidence=CheckExecutionEvidence(
                lines=[event.raw for event in kept],
                truncated=len(events) > len(kept),
            ),
            substrates_read=[SubstrateName.APPLICATION_LOGS],
        )
