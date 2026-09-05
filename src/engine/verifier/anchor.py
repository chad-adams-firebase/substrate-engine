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

The pronoun window (Fix Pass, R1 b′): kind-less pronouns — "it",
"its" — are unchecked by design, since "the rule that flags it" refers
to a rule while the prior turn established a supplier. But after an
anchor warn the drift is live in the conversation: the bank's breach
was warn → "How many findings has it produced?" → 197 verified; the
session's was warn → refusal → the same. So once a turn has been
warned, a kind-less pronoun is read against the surviving anchor of
the warned kind, until an unwarned answer establishes a new entity of
any kind — the newest entity being the pronoun's likeliest referent.
A refusal establishes nothing and keeps the window open; a fixed turn
count would close one record and not the other. Worst case inside the
window is a warn, [UNVERIFIED], never a verified wrong count.

What the check confirmed (Rider Pass): the same reading that warns
also knows when an arm positively matched the anchor — a filter on
its key, its name in the prose as a whole word. read_anchor returns
that beside the finding, with the one anchor value the arm matched,
bare, as the evidence spells it, so the harness can write it as the
About the router did not declare. A table that filtered on nothing is
silent but confirms nothing; a substring hit ("ava" in "available")
stays silent, as before, but confirms nothing either. MT-ABOUT's
turn 2 declared its About 3/5 with the content right 5/5: the check
had already read `rate_variance` in the prose every time.
"""

import re
from dataclasses import dataclass
from typing import Literal

from engine.tools.entities import (
    EntityCatalog,
    anaphor_kind,
    equality_literals,
    strip_kind_noun,
)
from engine.tools.envelope import Anchor, RunSqlOutput, ToolInvocation, TurnAnchors
from engine.verifier.models import DraftAnswer, PlausibilityFinding

CHECK = "anchor.entity_mismatch"


@dataclass(frozen=True)
class AnchorReading:
    """What the check read on an anaphoric turn with a prior anchor: the
    anchor, the finding if an arm contradicted it, and — when an arm
    positively confirmed the answer — which arm and the one anchor
    value it confirmed. A finding and a confirmation are exclusive; a
    table that filtered on nothing has neither. default_about is ""
    unless confirmed_by is "filter" or "prose": a declared about needs
    no default."""

    kind: str
    turn: int
    anchors: tuple[Anchor, ...]
    finding: PlausibilityFinding | None = None
    confirmed_by: Literal["declared", "filter", "prose"] | None = None
    default_about: str = ""


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


_PRONOUN = re.compile(r"\b(?:it|its)\b")


def is_kindless_pronoun(question: str) -> bool:
    """Whether the question refers back with "it" or "its" — the
    pronouns the window reads. "They", "those", "that" alone are not
    read: their referents are plural or clausal."""
    return _PRONOUN.search(question.casefold()) is not None


def open_window(prior: list[TurnAnchors]) -> TurnAnchors | None:
    """The warned record whose window is still open, or None. Newest
    first, the first record that either was warned (open, that kind)
    or established a column-bearing anchor of any kind (closed)
    decides. A warned turn wrote no anchors, so an anchor found after
    a warn came from an unwarned answer and is trusted."""
    for anchors in reversed(prior):
        if anchors.contradicted_kind:
            return anchors
        if any(anchor.column for anchor in anchors.entities):
            return None
    return None


def referent_kind(
    question: str, prior: list[TurnAnchors], catalog: EntityCatalog
) -> str | None:
    """The kind the question refers back to: the kind noun it names,
    else — for a kind-less pronoun inside an open window — the kind
    the warn was about. One reading, shared by the check, the
    harness's finalize (the declared about's kind) and the replay."""
    kind = anaphor_kind(question, catalog)
    if kind is not None:
        return kind
    if is_kindless_pronoun(question):
        window = open_window(prior)
        if window is not None:
            return window.contradicted_kind
    return None


def _whole_word(name: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def _confirming_value(
    anchors: list[Anchor], text: str, catalog: EntityCatalog
) -> str | None:
    """The anchor value the prose names as a whole word — a name column's
    value before a key's when both appear, then column order — or None
    when only a substring matched. One value, never a join: a joined
    About would re-check as a warn."""
    matched = [a for a in anchors if _whole_word(_norm(a.value), text)]
    if not matched:
        return None
    matched.sort(key=lambda a: (catalog.is_id_like(a.column), a.column))
    return matched[0].value


def read_anchor(
    *,
    question: str,
    about: str | None,
    draft: DraftAnswer,
    evidence: list[ToolInvocation],
    prior: list[TurnAnchors],
    catalog: EntityCatalog,
) -> AnchorReading | None:
    """The check's full reading, or None when there is nothing to read:
    no kind in the question and no open window, or no prior entity of
    the kind. The three arms decide in order and the first that decides,
    decides; each returns the reading with either a finding or a
    confirmation."""
    kind = anaphor_kind(question, catalog)
    window: TurnAnchors | None = None
    if kind is None:
        window = open_window(prior) if is_kindless_pronoun(question) else None
        if window is None:
            return None
        kind = window.contradicted_kind
    found = _anchor_of(kind, prior)
    if found is None:
        return None
    turn, anchors = found
    names = {_norm(a.value) for a in anchors}
    shown = " / ".join(dict.fromkeys(a.value for a in anchors))
    if window is None:
        established = f"the question refers to that {kind}; turn {turn}'s evidence established `{shown}`"
    else:
        established = (
            f"the question's pronoun follows turn {window.turn}'s anchor warning; "
            f"turn {turn}'s evidence established `{shown}`"
        )

    def reading(**fields) -> AnchorReading:
        return AnchorReading(kind=kind, turn=turn, anchors=tuple(anchors), **fields)

    def warn(detail: str) -> AnchorReading:
        return reading(
            finding=PlausibilityFinding(check=CHECK, severity="warn", detail=f"{established}, {detail}")
        )

    if about:
        # The router may spell the about with its kind noun in front —
        # "invoice 440" for an anchor {440, INV-00426} (MT-KEY, 0/5 on
        # exactly this). One article and one synonym of the kind come
        # off, then equality with one anchor name (Fix Pass, R2).
        if _norm(strip_kind_noun(about, kind, catalog)) in names:
            return reading(confirmed_by="declared")
        return warn(f"and this answer says it is about `{about}`")

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
                # The anchor's spelling, not the SQL's.
                return reading(confirmed_by="filter", default_about=by_column[literal.canonical])
            return warn(
                f"and this answer filters on `{literal.canonical} = '{literal.values[0]}'`"
            )

    if draft.kind == "prose":
        text = _norm(draft.text)
        if any(name in text for name in names):
            confirmed = _confirming_value(list(anchors), text, catalog)
            if confirmed is None:
                return reading()  # a substring hit: silent, as before, unconfirmed
            return reading(confirmed_by="prose", default_about=confirmed)
        return warn("and this answer never names it")
    return reading()


def check_anchor(
    *,
    question: str,
    about: str | None,
    draft: DraftAnswer,
    evidence: list[ToolInvocation],
    prior: list[TurnAnchors],
    catalog: EntityCatalog,
) -> PlausibilityFinding | None:
    """The finding alone — what the Verifier's ladder and the exposure
    replay read."""
    result = read_anchor(
        question=question, about=about, draft=draft,
        evidence=evidence, prior=prior, catalog=catalog,
    )
    return result.finding if result is not None else None
