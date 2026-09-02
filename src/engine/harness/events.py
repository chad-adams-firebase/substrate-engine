"""Status events: one emission, two destinations (Brief §8, §10.2).

Every graph node emits start/finish events. Each emit() lands the
event in two places at once: the in-memory trail that finalizes into
turn_log.status_events, and a live listener — the CLI's stderr
progress trail now, Phase 5's SSE stream later.

This is an in-process callback, not external I/O, so it is not a port.
The EventLog is a per-turn object handed to node closures; it is
deliberately NOT part of the graph state (callbacks don't checkpoint).
Timestamps live here rather than in tool envelopes by design — turn
timing belongs to the harness (envelope.py's no-timestamp rule).
"""

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class StatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "route" | "act" | "tool:<name>" | "draft" | "verify" | "finalize"
    node: str
    phase: Literal["start", "finish"]
    detail: str
    at: datetime
    # What the model actually wrote when it broke the protocol — for
    # the turn log and the inspector, never for the live trail line a
    # manager watches (detail stays short). None on every other event.
    raw_response: str | None = None


class StatusListener(Protocol):
    def __call__(self, event: StatusEvent) -> None: ...


class EventLog:
    def __init__(self, listener: StatusListener | None = None) -> None:
        self.events: list[StatusEvent] = []
        self._listener = listener

    def emit(
        self,
        node: str,
        phase: Literal["start", "finish"],
        detail: str,
        *,
        raw_response: str | None = None,
    ) -> None:
        event = StatusEvent(
            node=node,
            phase=phase,
            detail=detail,
            at=datetime.now(UTC),
            raw_response=raw_response,
        )
        self.events.append(event)
        if self._listener is not None:
            self._listener(event)

    def dump_json(self) -> str:
        """The trail as the JSON that lands in turn_log.status_events."""
        import json

        return json.dumps(
            [event.model_dump(mode="json") for event in self.events],
            separators=(",", ":"),
        )
