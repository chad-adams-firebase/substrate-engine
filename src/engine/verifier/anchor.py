"""The anchor check (Backlog Pass, gate verdict §7 item 2): when the
question refers back to an entity — "that rule", "this invoice", "the
supplier above" — and a prior turn's evidence established one of that
kind, the answer must be about it. The 30-turn session's turn 7
answered "What does that rule check?" about new_supplier when turn 6
had named line_note; the Verifier checked that new_supplier exists,
never that it was the rule under discussion.

Three readings of what the answer is about, in order — the router's
declared about; a filter literal on one of the kind's columns in this
turn's SQL; for prose, the anchor's name in the text — and the first
that decides, decides. A contradiction is a warn: the answer ships
[UNVERIFIED] with the anchor named, never silently and never refused.
No kind in the question, no prior entity of that kind, or an ambiguous
one (a multi-row table) is silent — the check must not manufacture
refusals of clean turns.
"""

from engine.tools.entities import EntityCatalog, anaphor_kind, equality_literals
from engine.tools.envelope import Anchor, RunSqlOutput, ToolInvocation, TurnAnchors
from engine.verifier.models import DraftAnswer, PlausibilityFinding

CHECK = "anchor.entity_mismatch"


def _norm(value: str) -> str:
    return value.strip().strip("`'\"").casefold().replace("_", " ")


def _anchor_of(kind: str, prior: list[TurnAnchors]) -> tuple[int, list[Anchor]] | None:
    """The most recent prior turn's entity of the kind: its evidence
    anchors (one per column, one entity by construction), else the
    router's declaration there. None when no prior turn established
    one — a multi-row table established nothing."""
    for anchors in reversed(prior):
        evidence = [a for a in anchors.entities if a.kind == kind and a.column]
        if evidence:
            return anchors.turn, evidence
        declared = [a for a in anchors.entities if a.kind == kind and a.source == "declared"]
        if declared:
            return anchors.turn, declared
    return None


def check_anchor(
    *,
    question: str,
    about: str | None,
    draft: DraftAnswer,
    evidence: list[ToolInvocation],
    prior: list[TurnAnchors],
    catalog: EntityCatalog,
) -> PlausibilityFinding | None:
    kind = anaphor_kind(question, catalog)
    if kind is None:
        return None
    found = _anchor_of(kind, prior)
    if found is None:
        return None
    turn, anchors = found
    names = {_norm(a.value) for a in anchors}
    shown = " / ".join(dict.fromkeys(a.value for a in anchors))
    established = f"the question refers to that {kind}; turn {turn}'s evidence established `{shown}`"

    if about:
        if _norm(about) in names:
            return None
        return PlausibilityFinding(
            check=CHECK, severity="warn",
            detail=f"{established}, and this answer says it is about `{about}`",
        )

    by_column = {a.column: a.value for a in anchors if a.column}
    for invocation in evidence:
        if invocation.status != "ok" or not isinstance(invocation.output, RunSqlOutput):
            continue
        for literal in equality_literals(invocation.output.sql, catalog):
            if literal.kind != kind or literal.canonical not in by_column:
                continue
            if len(literal.values) != 1:
                continue
            if _norm(literal.values[0]) == _norm(by_column[literal.canonical]):
                return None
            return PlausibilityFinding(
                check=CHECK, severity="warn",
                detail=(
                    f"{established}, and this answer filters on "
                    f"`{literal.canonical} = '{literal.values[0]}'`"
                ),
            )

    if draft.kind == "prose":
        text = _norm(draft.text)
        if any(name in text for name in names):
            return None
        return PlausibilityFinding(
            check=CHECK, severity="warn",
            detail=f"{established}, and this answer never names it",
        )
    return None
