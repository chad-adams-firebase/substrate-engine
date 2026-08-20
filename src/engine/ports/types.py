"""Shared pydantic types used by port interfaces.

These are the contract shapes that cross the port boundary. Keep them
minimal: a field with no consumer does not exist (CLAUDE.md).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Message(BaseModel):
    """One turn of LLM conversation input (OpenAI-compatible shape)."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


class ToolSpec(BaseModel):
    """A tool made available to the LLM for a completion call."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    """A tool invocation requested by the LLM in its response."""

    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """The result of one completion call."""

    content: str
    tool_calls: list[ToolCall] = []
    model: str


class User(BaseModel):
    """The identity on whose behalf the engine acts."""

    username: str
    display_name: str


class RunStatus(BaseModel):
    """Whether a target-app component ran, per the execution log.

    count is the number of matching run events in the window — a
    number the Verifier can mechanically reconcile (§9.2); the prose
    detail alone cannot be matched against a claim like "ran 3 times".
    matched_lines are the raw log lines behind the answer (capped by
    adapter settings; count keeps the true total) — receipts for the
    evidence bundle, moved out of LLM-visible output by the tool.
    """

    ran: bool
    count: int = 0
    detail: str
    matched_lines: list[str] = []


class LogEvent(BaseModel):
    """One structured log event crossing the ExecutionLogPort boundary
    (a contract model, not a naked dict — CLAUDE.md). raw is the
    verbatim line for the evidence bundle; fields are the event's
    remaining key=value pairs, name-keyed."""

    ts: datetime
    level: str
    logger: str
    event: str
    fields: dict[str, str] = {}
    raw: str


class TimeWindow(BaseModel):
    """A half-open time range [start, end) for log queries."""

    start: datetime
    end: datetime


class Workspace(BaseModel):
    """A user's private folder of conversations and draft Units."""

    id: int
    owner: str
    name: str
    created_at: datetime


class Conversation(BaseModel):
    """One chat thread inside a workspace. Its id doubles as the
    checkpointer thread key (Brief §8)."""

    id: int
    workspace_id: int
    title: str
    created_at: datetime


class TurnLogEntry(BaseModel):
    """One §12 turn_log row — the provenance record every turn writes.

    verifier_verdict and status_events cross this boundary as opaque
    JSON strings: the port stays neutral about harness/verifier model
    shapes, exactly the neutrality the TEXT columns encode.
    """

    conversation_id: int
    turn: int
    actor: str
    action: str  # "ask" in Phase 4; package/publish actions are Phase 6
    tools_used: list[str] = []
    substrates_read: list[str] = []
    evidence_bundle_ref: str | None = None
    verifier_verdict: str | None = None
    substrate_versions: list[str] = []
    status_events: str | None = None
    created_at: datetime


class UnitSummary(BaseModel):
    """A published Unit of Work, as answer_from_known_items surfaces
    it: enough to suggest ("this looks answered already"), never the
    full Unit — the library UI (Phase 6) owns reading."""

    id: int
    title: str
    state: str  # "published" | "canonical"
    author: str
    snippet: str = ""
