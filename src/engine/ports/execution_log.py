"""ExecutionLogPort — did the target app's components actually run?

Pluggable substrate; a pack may not enable it. Local adapter (Phase 3):
structured log files emitted by real target-app runs (the reference
app's simulation writes them). Real adapter (later phase): Splunk REST
search jobs.

Narrow by design: the port exposes intent-shaped methods only. The
component→query mapping (logger/event filters locally, SPL templates
on the Splunk side) lives in the adapter's pack-config settings, and
the LLM never generates a query — capability lives in registered
tools, never ad-hoc LLM freedom.

`key` narrows a run check to one unit of work (an invoice id, a file
name). It only means something for components whose template declares
which log field carries the key; asking with a key when no key_field
is configured is an ExecutionLogError, never a silently widened
answer.
"""

from typing import Protocol

from engine.ports.types import LogEvent, RunStatus, TimeWindow


class ExecutionLogError(Exception):
    """The question cannot be asked as posed — unknown component, a
    key for a component with no key_field, an unreadable log. The
    message names what is known so a caller (or an LLM fed the error)
    can repair the call."""


class ExecutionLogPort(Protocol):
    def did_run(self, component: str, key: str, window: TimeWindow) -> RunStatus: ...

    def recent_errors(
        self, component: str, window: TimeWindow
    ) -> list[LogEvent]: ...
