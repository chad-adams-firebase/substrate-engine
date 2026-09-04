"""Graph state and routing decisions.

TurnState is the LangGraph state schema (pydantic; the checkpointer's
JsonPlusSerializer round-trips it). The graph is strictly sequential,
so plain last-write-wins fields are correct — no reducers.

Durable across turns: history (one HistoryTurn per finished turn),
turn, and the running summary with the turn it reaches (Brief §10.3,
Phase 5 Block 4). begin() resets every per-turn field at the next
question. The router sees the summary plus every turn newer than it,
verbatim; the drafter sees only this turn's evidence.

A checkpoint written before Block 4 holds the history as (user,
assistant) Message pairs; upgrade_history reads those into records —
turn numbers by pair index, lossy but safe — so an existing work.db
keeps loading with no migration and no forced reset.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from engine.harness.outcomes import (
    reading_line,
    AnswerBody,
    AnswerOutcome,
    ClarifyOutcome,
    RefuseOutcome,
    TableAnswer,
    TurnOutcome,
)
from engine.ports.types import Message
from engine.tools.envelope import ToolInvocation
from engine.verifier.models import (
    AttemptRecord,
    InjectedSpan,
    PlausibilityRecord,
    VerifierVerdict,
)

HistoryKind = Literal["prose", "table", "refuse", "clarify", "escalate"]


class HistoryTurn(BaseModel):
    """One finished turn as the checkpoint remembers it: the question
    as asked and the answer as the transcript shows it — a prose
    answer's final text, a table's caption line, a refusal's reason.
    The turn number is explicit: pair-indexing breaks the first time a
    node raises after begin bumped turn, since that checkpoint persists.
    kind lets the summary scrub collect figures from prose answers
    only — a table's cells never enter the history."""

    model_config = ConfigDict(extra="forbid")

    turn: int
    question: str
    answer: str
    kind: HistoryKind


def transcript_text(outcome: TurnOutcome) -> str:
    """The answer as the history keeps it. A table contributes its
    caption — the SQL that produced it, which names the window, the
    grouping and the columns a follow-up resolves against — never its
    cells; the other outcomes contribute their message text."""
    if isinstance(outcome, AnswerOutcome):
        if isinstance(outcome.body, TableAnswer):
            line = reading_line(outcome.body)
            caption = outcome.body.caption or "result set"
            return f"[table: {line + ' ' if line else ''}{caption}]"
        return outcome.body.text
    if isinstance(outcome, RefuseOutcome):
        return f"[refused: {outcome.reason}]"
    if isinstance(outcome, ClarifyOutcome):
        return f"[clarify: {outcome.question}]"
    return f"[escalated: {outcome.reason}]"


def kind_of_outcome(outcome: TurnOutcome) -> HistoryKind:
    if isinstance(outcome, AnswerOutcome):
        return "table" if isinstance(outcome.body, TableAnswer) else "prose"
    return outcome.kind


# transcript_text's bracketed forms, read back by kind_of_transcript
# when a legacy pair carries only the text.
_TRANSCRIPT_PREFIXES: tuple[tuple[str, HistoryKind], ...] = (
    ("[table:", "table"),
    ("[refused:", "refuse"),
    ("[clarify:", "clarify"),
    ("[escalated:", "escalate"),
)


def kind_of_transcript(text: str) -> HistoryKind:
    for prefix, kind in _TRANSCRIPT_PREFIXES:
        if text.startswith(prefix):
            return kind
    return "prose"


def upgrade_history(items: Any) -> Any:
    """A checkpoint's history as HistoryTurn records, whatever layout
    it was written in. Pre-Block-4 checkpoints hold Message pairs (the
    serializer revives them as Message objects; a JSON fallback would
    hand over role/content dicts): consecutive (user, assistant) pairs
    become records numbered by pair index. Anything else — a record, or
    a dict pydantic can validate into one — passes through. A trailing
    unpaired message is dropped: finalize writes pairs atomically, so
    one cannot occur, and dropping is the safe reading if it did."""
    if not isinstance(items, list):
        return items
    upgraded: list[Any] = []
    pending: str | None = None
    for item in items:
        if isinstance(item, Message):
            role, content = item.role, item.content
        elif isinstance(item, dict) and "role" in item:
            role, content = item["role"], str(item.get("content", ""))
        else:
            upgraded.append(item)
            continue
        if role == "user":
            pending = content
        elif role == "assistant" and pending is not None:
            upgraded.append(
                HistoryTurn(
                    turn=len(upgraded) + 1,
                    question=pending,
                    answer=content,
                    kind=kind_of_transcript(content),
                )
            )
            pending = None
    return upgraded


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # str, not ToolName: the LLM picks names and can hallucinate one;
    # act() turns an unknown name into feedback, not a crash.
    name: str
    arguments: dict[str, Any]
    # The id the transcript's tool message answers: the provider's, or
    # one the router synthesizes when the response carried none.
    call_id: str = ""


class RouteDecision(BaseModel):
    """The router's parsed output: gather more evidence, or take a
    terminal direction. Every kind is a routing outcome, including
    refuse — fail-closed is not an error path."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tools", "answer", "refuse", "clarify", "escalate"]
    selections: list[ToolSelection] = []
    # answer only: prose drafting, or a table envelope passed through
    # untouched from evidence[evidence_index].
    answer_shape: Literal["prose", "table"] = "prose"
    evidence_index: int | None = None
    reading: str | None = None  # the reading a table answer names
    reason: str = ""  # refuse / escalate
    question: str = ""  # clarify
    what_would_work: str = ""  # refuse
    # The verb arrived as prose, not a tool call, and was read as the
    # call anyway (Polish Pass): the graph records that the channel
    # error was tolerated, and the eval counts it beside nudges.
    parsed_from_text: bool = False


class TurnState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Durable across turns.
    history: list[HistoryTurn] = []
    turn: int = 0
    # The running summary of the turns older than the verbatim window,
    # and the last turn it covers (0: no summary yet). Written by the
    # summarize node, never by begin.
    summary: str = ""
    summary_through_turn: int = 0

    # Per-turn, reset by the begin node.
    question: str = ""
    scratch: list[Message] = []  # router-loop working messages
    evidence: list[ToolInvocation] = []  # the §9 evidence bundle
    iterations: int = 0
    decision: RouteDecision | None = None
    draft: AnswerBody | None = None
    draft_raw: str | None = None  # placeholders intact, for retry context
    injected_spans: list[InjectedSpan] = []
    draft_feedback: list[str] = []
    draft_attempts: int = 0  # placeholder-resolution retries
    verify_attempt: int = 0  # verifier ladder attempts
    verifier_attempts: list[AttemptRecord] = []
    verifier_plausibility: list[PlausibilityRecord] = []
    judge_calls_total: int = 0
    verdict: VerifierVerdict | None = None
    outcome: TurnOutcome | None = None

    @field_validator("history", mode="before")
    @classmethod
    def _upgrade_legacy_history(cls, value: Any) -> Any:
        return upgrade_history(value)
