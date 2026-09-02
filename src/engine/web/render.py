"""Outcome → what a person sees: the server-side twin of app.js's
renderers (§10.5). The browser draws the cards and tables; this module
draws the same content as text — for tests, which cannot run the JS,
and for the turns endpoint (Block 3) and any text surface that shows a
past turn.

The card vocabulary lives here once and app.js repeats it verbatim
(test_web_render pins the two together). A refusal card carries the
reason and the remedy, both plain language; RefuseOutcome.detail is
the engineer's diagnosis and no card renders it — the inspector does.
"""

from pydantic import BaseModel, ConfigDict

from engine.harness.outcomes import TurnOutcome
from engine.harness.render import render_table_text

UNVERIFIED_BADGE = (
    "UNVERIFIED — this answer could not be fully checked against its evidence"
)
CARD_TITLES = {
    "refuse": "This can't be answered",
    "clarify": "One thing to clarify first",
    "escalate": "This needs a person",
}


class Card(BaseModel):
    """A fail-closed outcome as the page shows it: a kind (the CSS
    class), a title, and labeled lines — every one plain language."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    title: str
    fields: list[tuple[str, str]]


def card_for(outcome: TurnOutcome) -> Card | None:
    """The card for a refuse/clarify/escalate outcome; None for an
    answer, which renders as a body, not a card."""
    if outcome.kind == "refuse":
        fields = [("Why", outcome.reason)]
        if outcome.what_would_work:
            fields.append(("What would work", outcome.what_would_work))
        return Card(kind="refuse", title=CARD_TITLES["refuse"], fields=fields)
    if outcome.kind == "clarify":
        return Card(
            kind="clarify",
            title=CARD_TITLES["clarify"],
            fields=[("Question", outcome.question)],
        )
    if outcome.kind == "escalate":
        return Card(
            kind="escalate",
            title=CARD_TITLES["escalate"],
            fields=[("Why", outcome.reason)],
        )
    return None


def render_outcome_text(outcome: TurnOutcome) -> str:
    """The outcome as plain text, in the page's order: the unverified
    badge, then the markdown or the table with its caption; or the
    card's title and lines."""
    card = card_for(outcome)
    if card is not None:
        lines = [card.title]
        lines.extend(f"{label}: {text}" for label, text in card.fields)
        return "\n".join(lines)
    parts: list[str] = []
    if outcome.verification == "unverified":
        parts.append(UNVERIFIED_BADGE)
    if outcome.body.kind == "table":
        parts.append(render_table_text(outcome.body.table))
        if outcome.body.caption:
            parts.append(f"({outcome.body.caption})")
    else:
        parts.append(outcome.body.text)
    return "\n".join(parts)
