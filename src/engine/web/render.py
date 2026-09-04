"""Outcome → what a person sees: the server-side twin of app.js's
renderers (§10.5). The browser draws the cards, tables and chips; this
module draws the same content as text — for tests, which cannot run
the JS, and for the text form of GET /api/conversations/<id>/turns
(Block 3), the surface that shows a past conversation without a
browser.

The card vocabulary and the chip vocabulary live here once and app.js
repeats them verbatim (test_web_render pins the two together). A
refusal card carries the reason and the remedy, both plain language;
RefuseOutcome.detail is the engineer's diagnosis and no card renders
it — the inspector does.
"""

import json
import math

from pydantic import BaseModel, ConfigDict

from engine.harness.events import StatusEvent
from engine.harness.outcomes import TurnOutcome, loads_outcome, about_line, reading_line
from engine.harness.render import render_table_text
from engine.ports.types import Conversation, TurnLogEntry

UNVERIFIED_BADGE = (
    "UNVERIFIED — this answer could not be fully checked against its evidence"
)
CARD_TITLES = {
    "refuse": "This can't be answered",
    "clarify": "One thing to clarify first",
    "escalate": "This needs a person",
}
CHIP_LABELS = {
    "verified": "✓ Verified",
    "unverified": "⚠ Unverified",
    "refuse": "⊘ Refused",
    "clarify": "? Clarify",
    "escalate": "↑ Escalated",
}
QUESTION_NOT_RECORDED = "(question not recorded)"
OUTCOME_NOT_RECORDED = "(outcome not recorded)"


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
        if about_line(outcome.body):
            parts.append(about_line(outcome.body))
        if reading_line(outcome.body):
            parts.append(reading_line(outcome.body))
        if outcome.body.caption:
            parts.append(f"({outcome.body.caption})")
    else:
        parts.append(outcome.body.text)
        if about_line(outcome.body):
            parts.append(about_line(outcome.body))
    return "\n".join(parts)


class ToolTally(BaseModel):
    """What the chip counts: invocations that returned evidence, errored
    invocations a later call of the same tool followed (retries), and
    errored invocations nothing followed (failed)."""

    model_config = ConfigDict(extra="forbid")

    ok: int
    retries: int
    failed: int


def tool_tally(events: list[StatusEvent]) -> ToolTally:
    """Read from the trail, not from tools_used: a bounced run_sql and
    its retry are two invocations of one tool, and the chip says
    "1 tool · 1 retry", not "2 tools" (Block 3, play session finding).
    A `tool:<name>` finish event reads `evidence[i] ok` or `error: …`;
    an unknown-tool skip is neither and is not an invocation."""
    finishes = [
        (event.node, event.detail)
        for event in events
        if event.phase == "finish" and event.node.startswith("tool:")
    ]
    ok = retries = failed = 0
    for index, (node, detail) in enumerate(finishes):
        if detail.startswith("evidence["):
            ok += 1
        elif detail.startswith("error:"):
            if any(later == node for later, _ in finishes[index + 1 :]):
                retries += 1
            else:
                failed += 1
    return ToolTally(ok=ok, retries=retries, failed=failed)


def elapsed_seconds(events: list[StatusEvent]) -> int:
    """First event to last, whole seconds, at least 1 when the trail has
    two events; floor of x + 0.5 so a tie rounds the way app.js does."""
    if len(events) < 2:
        return 0
    delta = (events[-1].at - events[0].at).total_seconds()
    return max(1, math.floor(delta + 0.5))


def chip_key(
    outcome: TurnOutcome | None,
    events: list[StatusEvent],
    verdict_disposition: str | None = None,
) -> str | None:
    """Which chip a turn gets. With an outcome: its verification (an
    answer) or its kind. Without one — a row written before the turn
    log kept outcomes (Polish Pass) — the trail's finalize event says
    what ended the turn, and the recorded verdict says how an answer
    fared; a trail with no finalize gets no chip."""
    if outcome is not None:
        return outcome.verification if outcome.kind == "answer" else outcome.kind
    ended = next(
        (
            event.detail
            for event in reversed(events)
            if event.node == "finalize" and event.phase == "finish"
        ),
        None,
    )
    if ended is None:
        return None
    if ended == "answer":
        return verdict_disposition if verdict_disposition in CHIP_LABELS else "unverified"
    return ended if ended in CHIP_LABELS else None


def chip_label(
    outcome: TurnOutcome | None,
    events: list[StatusEvent],
    verdict_disposition: str | None = None,
) -> str:
    """The collapsed trail: `✓ Verified · 1 tool · 1 retry · 14s`."""
    key = chip_key(outcome, events, verdict_disposition)
    assert key is not None, "chip_label needs a chip_key"
    tally = tool_tally(events)
    parts = [CHIP_LABELS[key], f"{tally.ok} tool{'' if tally.ok == 1 else 's'}"]
    if tally.retries:
        parts.append(f"{tally.retries} {'retry' if tally.retries == 1 else 'retries'}")
    if tally.failed:
        parts.append(f"{tally.failed} failed")
    seconds = elapsed_seconds(events)
    if seconds:
        parts.append(f"{seconds}s")
    return " · ".join(parts)


def verdict_disposition_of(entry: TurnLogEntry) -> str | None:
    """The recorded verdict's disposition, read without a model: the
    column is opaque JSON at the port, and the chip needs one word."""
    if not entry.verifier_verdict:
        return None
    verdict = json.loads(entry.verifier_verdict)
    disposition = verdict.get("disposition") if isinstance(verdict, dict) else None
    return disposition if isinstance(disposition, str) else None


def events_of(entry: TurnLogEntry) -> list[StatusEvent]:
    return [
        StatusEvent.model_validate(event)
        for event in json.loads(entry.status_events or "[]")
    ]


def render_turns_text(conversation: Conversation, entries: list[TurnLogEntry]) -> str:
    """A past conversation as text, one block per turn: the chip line,
    the question, the outcome as the page shows it. Rows written before
    the turn log carried question and outcome say so."""
    lines = [f"conversation {conversation.id} · {conversation.title}", ""]
    for entry in entries:
        events = events_of(entry)
        outcome = loads_outcome(entry.outcome) if entry.outcome else None
        head = f"turn {entry.turn}"
        if chip_key(outcome, events, verdict_disposition_of(entry)) is not None:
            head += " · " + chip_label(outcome, events, verdict_disposition_of(entry))
        lines.append(head)
        lines.append(f"> {entry.question}" if entry.question else f"> {QUESTION_NOT_RECORDED}")
        lines.append(
            render_outcome_text(outcome) if outcome is not None else OUTCOME_NOT_RECORDED
        )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
