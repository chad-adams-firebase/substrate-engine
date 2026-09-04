"""engine/web/render.py: every outcome kind as the page shows it,
rendered server-side so tests can read what the browser draws. The
browser JS is not unit-tested; this pins the card vocabulary the two
share and the rule that engineer detail never reaches a card."""

from datetime import UTC, datetime

from engine.harness.events import StatusEvent
from engine.harness.outcomes import (
    AnswerOutcome,
    ClarifyOutcome,
    EscalateOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
    dumps_outcome,
)
from engine.harness.render import NO_ROWS
from engine.ports.types import Conversation, TurnLogEntry
from engine.tools.envelope import ColumnFormat, Table
from engine.web.app import STATIC_DIR
from engine.web.render import (
    CARD_TITLES,
    CHIP_LABELS,
    OUTCOME_NOT_RECORDED,
    QUESTION_NOT_RECORDED,
    UNVERIFIED_BADGE,
    card_for,
    chip_label,
    render_outcome_text,
    render_turns_text,
    tool_tally,
)


def test_refusal_card_carries_reason_and_remedy_never_detail():
    outcome = RefuseOutcome(
        reason="I couldn't gather enough evidence to answer this reliably.",
        what_would_work="A narrower question.",
        detail="tool budget exhausted: 8 router steps without a terminal decision",
    )
    card = card_for(outcome)
    assert card.kind == "refuse" and card.title == "This can't be answered"
    assert card.fields == [
        ("Why", outcome.reason),
        ("What would work", "A narrower question."),
    ]
    text = render_outcome_text(outcome)
    assert "8 router steps" not in text and "budget" not in text
    assert text == (
        "This can't be answered\n"
        "Why: I couldn't gather enough evidence to answer this reliably.\n"
        "What would work: A narrower question."
    )


def test_refusal_without_a_remedy_omits_the_line():
    card = card_for(RefuseOutcome(reason="no"))
    assert card.fields == [("Why", "no")]


def test_clarify_and_escalate_cards():
    clarify = card_for(ClarifyOutcome(question="Which week do you mean?"))
    assert clarify.title == "One thing to clarify first"
    assert clarify.fields == [("Question", "Which week do you mean?")]
    escalate = EscalateOutcome(reason="a policy call")
    assert card_for(escalate).title == "This needs a person"
    assert render_outcome_text(escalate) == "This needs a person\nWhy: a policy call"


def test_answers_render_as_bodies_not_cards():
    prose = AnswerOutcome(body=MarkdownAnswer(text="146 of 161."), verification="verified")
    assert card_for(prose) is None
    assert render_outcome_text(prose) == "146 of 161."

    table = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["supplier", "total", "wait"],
                rows=[{"supplier": "RVX01", "total": 8308.92139244107, "wait": None}],
                total_row_count=1,
                column_formats={"total": ColumnFormat(kind="money", symbol="$")},
            ),
            caption="SELECT ...",
        ),
        verification="unverified",
    )
    text = render_outcome_text(table)
    lines = text.splitlines()
    assert lines[0] == UNVERIFIED_BADGE
    assert "$8,308.92" in lines[3] and "—" in lines[3]
    assert lines[-1] == "(SELECT ...)"
    # Close Pass: the reading, when named, is the line above the SQL.
    named = table.model_copy(
        update={"body": table.body.model_copy(update={"reading": "substantive"})}
    )
    named_lines = render_outcome_text(named).splitlines()
    assert named_lines[-2:] == ["Reading: substantive.", "(SELECT ...)"]


def test_the_page_repeats_the_card_vocabulary_verbatim():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for title in CARD_TITLES.values():
        assert title in script, title
    assert UNVERIFIED_BADGE in script
    # The engineer's diagnosis never reaches a card: the transcript
    # renderers (everything before the inspector section) never read
    # outcome.detail; the inspector does, labelled as the diagnosis.
    transcript_half, inspector_half = script.split("// ---- inspector: the receipts")
    assert "outcome.detail" not in transcript_half
    assert "outcome.detail" in inspector_half and '"Diagnosis"' in inspector_half


def test_an_empty_table_outcome_reads_no_rows_matched_on_both_surfaces():
    """The page and the text twin say the same sentence; app.js repeats
    render.py's NO_ROWS verbatim (pinned below with the rest of the
    card vocabulary)."""
    outcome = AnswerOutcome(
        body=TableAnswer(
            table=Table(columns=[], rows=[], total_row_count=0),
            caption="SELECT ... WHERE to_status = 'REJECTED'",
        ),
        verification="unverified",
    )
    lines = render_outcome_text(outcome).splitlines()
    assert lines == [
        UNVERIFIED_BADGE,
        NO_ROWS,
        "(SELECT ... WHERE to_status = 'REJECTED')",
    ]
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert f'const NO_ROWS = "{NO_ROWS}"' in script


# --- the chip and the turns text form (Block 3) ---------------------------


def _events(*steps, start="2026-09-02T10:00:00+00:00", seconds_apart=1):
    """(node, phase, detail) triples, one second apart by default."""
    base = datetime.fromisoformat(start)
    return [
        StatusEvent(
            node=node,
            phase=phase,
            detail=detail,
            at=base.replace(second=(base.second + index * seconds_apart) % 60),
        )
        for index, (node, phase, detail) in enumerate(steps)
    ]


VERIFIED = AnswerOutcome(body=MarkdownAnswer(text="146."), verification="verified")


def test_a_bounced_and_retried_call_is_one_tool_and_one_retry():
    """The play-session finding: run_sql bounced the SQL-shaped question
    and the router resent it in English — two invocations, one tool."""
    events = _events(
        ("route", "start", "Consulting router (step 1)…"),
        ("route", "finish", "decision: tools"),
        ("tool:run_sql", "start", "Running run_sql…"),
        ("tool:run_sql", "finish", "error: run_sql writes its own SQL — send the English question"),
        ("route", "finish", "decision: tools"),
        ("tool:run_sql", "start", "Running run_sql…"),
        ("tool:run_sql", "finish", "evidence[1] ok"),
        ("finalize", "finish", "answer"),
    )
    tally = tool_tally(events)
    assert (tally.ok, tally.retries, tally.failed) == (1, 1, 0)
    assert chip_label(VERIFIED, events) == "✓ Verified · 1 tool · 1 retry · 7s"


def test_two_clean_calls_are_two_tools_and_an_unretried_error_is_failed():
    events = _events(
        ("tool:app_primer", "finish", "evidence[0] ok"),
        ("tool:run_sql", "finish", "evidence[1] ok"),
        ("tool:check_execution", "finish", "error: unknown component"),
        ("tool:nonesuch", "finish", "unknown tool — skipped"),
        ("finalize", "finish", "refuse"),
    )
    assert tool_tally(events).model_dump() == {"ok": 2, "retries": 0, "failed": 1}
    refused = RefuseOutcome(reason="r")
    assert chip_label(refused, events) == "⊘ Refused · 2 tools · 1 failed · 4s"
    assert chip_label(refused, []) == "⊘ Refused · 0 tools"
    unverified = AnswerOutcome(body=MarkdownAnswer(text="x"), verification="unverified")
    assert chip_label(unverified, events[:1]) == "⚠ Unverified · 1 tool"


def test_the_page_repeats_the_chip_vocabulary_and_the_tally_rule():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for label in CHIP_LABELS.values():
        assert label in script, label
    assert "function toolTally" in script
    assert 'startsWith("evidence[")' in script and 'startsWith("error:")' in script
    assert '"retry" : "retries"' in script and '"failed"' in script
    # Elapsed seconds round the same way: floor of x + 0.5.
    assert "Math.floor(" in script and "+ 0.5)" in script


def test_turns_text_form_renders_every_turn_and_says_when_a_row_predates_the_columns():
    conversation = Conversation(
        id=3, workspace_id=1, title="flag rates", created_at=datetime.now(UTC)
    )
    events = _events(
        ("route", "start", "…"), ("tool:run_sql", "finish", "evidence[0] ok"),
        ("finalize", "finish", "answer"),
    )
    table = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["n", "total"],
                rows=[{"n": 78, "total": 8308.92139}],
                total_row_count=1,
                column_formats={"total": ColumnFormat(kind="money", symbol="$")},
            ),
            caption="SELECT ...",
        ),
        verification="verified",
    )
    entries = [
        TurnLogEntry(
            conversation_id=3, turn=1, actor="dev", action="ask",
            question="How many are open?", outcome=dumps_outcome(table),
            status_events="[" + ",".join(e.model_dump_json() for e in events) + "]",
            created_at=datetime.now(UTC),
        ),
        TurnLogEntry(
            conversation_id=3, turn=2, actor="dev", action="ask",
            question="Deploy it.", outcome=dumps_outcome(RefuseOutcome(reason="no", detail="diag")),
            status_events="[]", created_at=datetime.now(UTC),
        ),
        TurnLogEntry(  # written before Block 3: no question, no outcome
            conversation_id=3, turn=3, actor="dev", action="ask",
            created_at=datetime.now(UTC),
        ),
    ]
    text = render_turns_text(conversation, entries)
    assert text == (
        "conversation 3 · flag rates\n"
        "\n"
        "turn 1 · ✓ Verified · 1 tool · 2s\n"
        "> How many are open?\n"
        "n   total    \n"
        "--  ---------\n"
        "78  $8,308.92\n"
        "(SELECT ...)\n"
        "\n"
        "turn 2 · ⊘ Refused · 0 tools\n"
        "> Deploy it.\n"
        "This can't be answered\n"
        "Why: no\n"
        "\n"
        "turn 3\n"
        f"> {QUESTION_NOT_RECORDED}\n"
        f"{OUTCOME_NOT_RECORDED}\n"
    )
    assert "diag" not in text  # the diagnosis never reaches a text card either


def test_a_legacy_turn_gets_its_chip_from_the_trail():
    """Polish Pass: rows written before the turn log kept outcomes
    still hold the trail, the verdict and the evidence ref; the chip
    reads the finalize event and the verdict's disposition, so the
    inspector opens on them. A trail with no finalize gets no chip."""
    from engine.web.render import chip_key

    now = datetime.now(UTC)

    def event(node, phase, detail):
        return StatusEvent(node=node, phase=phase, detail=detail, at=now)

    answered = [
        event("tool:run_sql", "finish", "evidence[0] ok"),
        event("verify", "finish", "verdict: verified"),
        event("finalize", "finish", "answer"),
    ]
    assert chip_key(None, answered, "verified") == "verified"
    assert chip_key(None, answered, "unverified") == "unverified"
    assert chip_key(None, answered, None) == "unverified"  # an answer with no verdict
    assert chip_key(None, [event("finalize", "finish", "refuse")], None) == "refuse"
    assert chip_key(None, [event("route", "start", "Consulting router")], None) is None
    assert chip_key(None, [], "verified") is None

    conversation = Conversation(id=4, workspace_id=1, title="legacy", created_at=now)
    entries = [
        TurnLogEntry(  # written before Block 3: no question, no outcome
            conversation_id=4, turn=1, actor="dev", action="ask",
            verifier_verdict='{"disposition": "verified", "mode": "table_passthrough"}',
            status_events="[" + ",".join(e.model_dump_json() for e in answered) + "]",
            evidence_bundle_ref="abc", created_at=now,
        ),
        TurnLogEntry(
            conversation_id=4, turn=2, actor="dev", action="ask",
            status_events="[" + event("finalize", "finish", "refuse").model_dump_json() + "]",
            created_at=now,
        ),
    ]
    text = render_turns_text(conversation, entries)
    assert text == (
        "conversation 4 · legacy\n"
        "\n"
        "turn 1 · ✓ Verified · 1 tool · 1s\n"
        f"> {QUESTION_NOT_RECORDED}\n"
        f"{OUTCOME_NOT_RECORDED}\n"
        "\n"
        "turn 2 · ⊘ Refused · 0 tools\n"
        f"> {QUESTION_NOT_RECORDED}\n"
        f"{OUTCOME_NOT_RECORDED}\n"
    )


def test_the_page_renders_the_reading_line_the_text_surfaces_print():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert '"Reading: " + outcome.body.reading + "."' in script
