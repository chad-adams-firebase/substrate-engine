"""Control tools: the router's terminal verbs.

Four synthetic tool specs appended to the registry's real specs for
the router call only. They are routing outcomes, not capabilities, so
they never enter ToolRegistry — the closed §6 surface stays closed.
Making them tools (rather than parsed prose) means every terminal
direction, including fail-closed, arrives schema-validated.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from engine.harness.state import RouteDecision, ToolSelection
from engine.ports.types import LLMResponse, ToolCall, ToolSpec

# A control verb spelled as the whole response —
# give_answer({"shape":"prose"}) — with a JSON object (or nothing) in
# the parentheses. Read as the call it plainly is (Polish Pass): B2's
# router wrote exactly this on nine runs, in the wrong channel, and was
# nudged into budget exhaustion four times per rep. No "Requested: "
# prefix is tolerated: nothing produces that echo any more — the loop
# transcript is native tool messages — so a response wearing it is
# the model completing a format it should never have seen.
_TEXT_FORM_CALL = re.compile(
    r"^\s*(give_answer|refuse|clarify|escalate)\s*\((.*)\)\s*$",
    re.DOTALL,
)


class GiveAnswerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: Literal["prose", "table"] = "prose"
    # Which evidence item's output ships untouched when shape="table":
    # the index shown alongside each tool result in the loop feedback.
    evidence_index: int | None = None
    # The reading the chosen result's SQL computed, from the readings
    # that result lists (Close Pass). Validated by the graph, which has
    # the evidence: an undeclared name is nudged, a missing one is not.
    reading: str | None = Field(
        default=None,
        description=(
            "shape='table' only: when the chosen run_sql result lists "
            "readings, the name of the one its SQL computed, exactly as "
            "listed."
        ),
    )

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
        "untouched; shape='prose' drafts a grounded explanation. When "
        "the chosen run_sql result lists readings, also pass reading="
        "<one of them>: the reading its SQL computed, exactly as listed."
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


def text_form_call(content: str) -> ToolCall | None:
    """The control call a prose response spells out, or None. Only the
    whole response, only a control verb (a real tool written as text
    stays a violation — the closed surface is entered by tool calls),
    only a JSON object or nothing as arguments."""
    match = _TEXT_FORM_CALL.match(content or "")
    if match is None:
        return None
    name, body = match.group(1), match.group(2).strip()
    if not body:
        return ToolCall(name=name, arguments={})
    try:
        arguments = json.loads(body)
    except ValueError:
        return None
    if not isinstance(arguments, dict):
        return None
    return ToolCall(name=name, arguments=arguments)


def parse_route(response: LLMResponse) -> RouteDecision:
    """A router response, parsed into a decision.

    Real tool calls win over control calls in the same response: a
    model that asks for run_sql and give_answer together has not seen
    the rows yet. Control-only responses take the first control call; a
    control verb written as text is read as that call and the decision
    says so (parsed_from_text), so the graph can leave a trace. Anything
    else — prose, no calls, malformed control arguments — is a protocol
    violation.
    """
    real = [c for c in response.tool_calls if c.name not in CONTROL_NAMES]
    if real:
        return RouteDecision(
            kind="tools",
            selections=[
                ToolSelection(name=c.name, arguments=c.arguments, call_id=c.id)
                for c in real
            ],
        )

    control = [c for c in response.tool_calls if c.name in CONTROL_NAMES]
    parsed_from_text = False
    if control:
        call = control[0]
    else:
        spelled = text_form_call(response.content)
        if spelled is None:
            raise RouteProtocolViolation(
                "Respond by calling one of the provided tools — either an "
                "evidence tool, or give_answer / refuse / clarify / escalate.",
                raw_response=response.content,
            )
        call = spelled
        parsed_from_text = True

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
            reading=args.reading,
            parsed_from_text=parsed_from_text,
        )
    if isinstance(args, RefuseArgs):
        return RouteDecision(
            kind="refuse",
            reason=args.reason,
            what_would_work=args.what_would_work,
            parsed_from_text=parsed_from_text,
        )
    if isinstance(args, ClarifyArgs):
        return RouteDecision(
            kind="clarify", question=args.question, parsed_from_text=parsed_from_text
        )
    return RouteDecision(
        kind="escalate", reason=args.reason, parsed_from_text=parsed_from_text
    )


def control_verb(decision: RouteDecision) -> str:
    """The verb a terminal decision came from, for the trace."""
    return {"answer": "give_answer"}.get(decision.kind, decision.kind)
