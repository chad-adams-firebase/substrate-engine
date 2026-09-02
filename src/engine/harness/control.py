"""Control tools: the router's terminal verbs.

Four synthetic tool specs appended to the registry's real specs for
the router call only. They are routing outcomes, not capabilities, so
they never enter ToolRegistry — the closed §6 surface stays closed.
Making them tools (rather than parsed prose) means every terminal
direction, including fail-closed, arrives schema-validated.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from engine.harness.state import RouteDecision, ToolSelection
from engine.ports.types import LLMResponse, ToolSpec


class GiveAnswerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: Literal["prose", "table"] = "prose"
    # Which evidence item's output ships untouched when shape="table":
    # the index shown alongside each tool result in the loop feedback.
    evidence_index: int | None = None

    @model_validator(mode="after")
    def _table_needs_an_index(self) -> "GiveAnswerArgs":
        if self.shape == "table" and self.evidence_index is None:
            raise ValueError("shape='table' requires evidence_index")
        return self


class RefuseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    what_would_work: str = ""


class ClarifyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


class EscalateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


_CONTROL_MODELS: dict[str, type[BaseModel]] = {
    "give_answer": GiveAnswerArgs,
    "refuse": RefuseArgs,
    "clarify": ClarifyArgs,
    "escalate": EscalateArgs,
}

_CONTROL_DESCRIPTIONS = {
    "give_answer": (
        "The gathered evidence answers the question. shape='table' with "
        "evidence_index=N returns that tool result directly as a table, "
        "untouched; shape='prose' drafts a grounded explanation."
    ),
    "refuse": (
        "The question is out of scope, asks for action rather than "
        "information, or cannot be answered after the relevant evidence "
        "tools have been tried — for a code or workflow question that "
        "means the code knowledge graph and source, not only the primer "
        "or the documents. Refuse only when the tool surface is exhausted "
        "or the question is out of scope, never because the first tool "
        "returned nothing. State why, and what would work instead — in "
        "plain language for a business reader, never tool names, step "
        "counts, or SQL."
    ),
    "clarify": (
        "The question is ambiguous; ask one clarifying question, phrased "
        "for a business reader."
    ),
    "escalate": "Answering needs a human decision; say why.",
}

CONTROL_NAMES = frozenset(_CONTROL_MODELS)


def control_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=_CONTROL_DESCRIPTIONS[name],
            input_schema=model.model_json_schema(),
        )
        for name, model in sorted(_CONTROL_MODELS.items())
    ]


class RouteProtocolViolation(Exception):
    """The router response did not follow the tool-call contract; the
    message is the nudge fed back before the next iteration, and
    raw_response is what the router actually wrote — recorded in
    provenance (never on the live trail line) so the next diagnosis of
    a prose-instead-of-a-call habit is one read (the post-Block-2 B2)."""

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


def parse_route(response: LLMResponse) -> RouteDecision:
    """A router response, parsed into a decision.

    Real tool calls win over control calls in the same response: a
    model that asks for run_sql and give_answer together has not seen
    the rows yet. Control-only responses take the first control call.
    Anything else — prose, no calls, malformed control arguments — is
    a protocol violation.
    """
    real = [c for c in response.tool_calls if c.name not in CONTROL_NAMES]
    if real:
        return RouteDecision(
            kind="tools",
            selections=[
                ToolSelection(name=c.name, arguments=c.arguments) for c in real
            ],
        )

    control = [c for c in response.tool_calls if c.name in CONTROL_NAMES]
    if not control:
        raise RouteProtocolViolation(
            "Respond by calling one of the provided tools — either an "
            "evidence tool, or give_answer / refuse / clarify / escalate.",
            raw_response=response.content,
        )

    call = control[0]
    try:
        args = _CONTROL_MODELS[call.name].model_validate(call.arguments)
    except ValidationError as exc:
        raise RouteProtocolViolation(
            f"Invalid {call.name} arguments: "
            + "; ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ),
            raw_response=f"{call.name}({json.dumps(call.arguments, sort_keys=True)})",
        ) from exc

    if isinstance(args, GiveAnswerArgs):
        return RouteDecision(
            kind="answer",
            answer_shape=args.shape,
            evidence_index=args.evidence_index,
        )
    if isinstance(args, RefuseArgs):
        return RouteDecision(
            kind="refuse", reason=args.reason, what_would_work=args.what_would_work
        )
    if isinstance(args, ClarifyArgs):
        return RouteDecision(kind="clarify", question=args.question)
    return RouteDecision(kind="escalate", reason=args.reason)
