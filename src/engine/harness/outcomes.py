"""Terminal outcomes: answer, refuse, clarify, escalate — first-class
typed payloads, never error strings (Brief §8). Phase 4 answers are
markdown or table; chart/code join the AnswerBody union when Phase 5
renders them.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from engine.harness.events import StatusEvent
from engine.tools.envelope import Table
from engine.verifier.models import VerifierVerdict


class MarkdownAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["markdown"] = "markdown"
    # Placeholder-resolved final text — exactly what the Verifier saw.
    text: str


class TableAnswer(BaseModel):
    """A data-shaped answer passed through untouched by the model
    (Brief §6): numbers travel store to screen."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"] = "table"
    table: Table
    caption: str = ""  # e.g. the SQL that produced it


AnswerBody = Annotated[MarkdownAnswer | TableAnswer, Field(discriminator="kind")]


class AnswerOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer"] = "answer"
    body: AnswerBody
    # "unverified" is the explicit downgrade label of the §9 ladder —
    # never silent, rendered by every surface that shows the answer.
    verification: Literal["verified", "unverified"]


class RefuseOutcome(BaseModel):
    """Fail-closed: what can't be answered, why, and what would work —
    a first-class outcome, styled as a card in Phase 5 (§10.5). The
    card speaks to the person who asked: reason and what_would_work
    are plain language, never a step count or a tolerance. The
    engineer's diagnosis — which bound tripped, by how much — travels
    in detail, which no card renders; the CLI prints it and the
    Phase 5 inspector reads it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["refuse"] = "refuse"
    reason: str
    what_would_work: str = ""
    detail: str = ""


class ClarifyOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["clarify"] = "clarify"
    question: str


class EscalateOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["escalate"] = "escalate"
    reason: str


TurnOutcome = Annotated[
    AnswerOutcome | RefuseOutcome | ClarifyOutcome | EscalateOutcome,
    Field(discriminator="kind"),
]

# The outcome ladder as exit-code equivalents — outcome semantics, so
# it lives with the outcomes: the CLI exits with these, and the eval
# harness records them per run (1 is reserved for errors).
EXIT_CODES = {
    ("answer", "verified"): 0,
    ("answer", "unverified"): 2,
    ("refuse", None): 3,
    ("clarify", None): 4,
    ("escalate", None): 5,
}


def exit_code_of(outcome: "TurnOutcome") -> int:
    return EXIT_CODES[
        (
            outcome.kind,
            outcome.verification if outcome.kind == "answer" else None,
        )
    ]


_OUTCOME = TypeAdapter(TurnOutcome)


def dumps_outcome(outcome: "TurnOutcome") -> str:
    """The outcome as the JSON turn_log.outcome stores — opaque to the
    port, read back by loads_outcome for every surface that shows a
    past turn."""
    return _OUTCOME.dump_json(outcome).decode("utf-8")


def loads_outcome(text: str) -> "TurnOutcome":
    return _OUTCOME.validate_json(text)


class TurnResult(BaseModel):
    """What AskSession returns per turn — outcome plus the provenance
    handles the CLI (and Phase 5) surface alongside it."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: int
    turn: int
    outcome: TurnOutcome
    tools_used: list[str] = []
    evidence_bundle_ref: str | None = None
    verdict: VerifierVerdict | None = None
    events: list[StatusEvent] = []
