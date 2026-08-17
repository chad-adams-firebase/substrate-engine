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
    """

    ran: bool
    count: int = 0
    detail: str


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


class UnitSummary(BaseModel):
    """A published Unit of Work, as answer_from_known_items surfaces
    it: enough to suggest ("this looks answered already"), never the
    full Unit — the library UI (Phase 6) owns reading."""

    id: int
    title: str
    state: str  # "published" | "canonical"
    author: str
    snippet: str = ""
