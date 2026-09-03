"""Graph state and routing decisions.

TurnState is the LangGraph state schema (pydantic; the checkpointer's
JsonPlusSerializer round-trips it). The graph is strictly sequential,
so plain last-write-wins fields are correct — no reducers.

Only history and turn carry meaning across turns; begin() resets every
per-turn field at the next question. Context stays deliberately simple
this phase (Brief §10.3 summaries are Phase 5): the router sees the
running history plus this turn's working messages, the drafter sees
only this turn's evidence.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from engine.harness.outcomes import AnswerBody, TurnOutcome
from engine.ports.types import Message
from engine.tools.envelope import ToolInvocation
from engine.verifier.models import (
    AttemptRecord,
    InjectedSpan,
    PlausibilityRecord,
    VerifierVerdict,
)


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # str, not ToolName: the LLM picks names and can hallucinate one;
    # act() turns an unknown name into feedback, not a crash.
    name: str
    arguments: dict[str, Any]


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
    history: list[Message] = []
    turn: int = 0

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
