"""Harness contract models: event dual-destination, outcome unions,
verdict round-trip."""

import json

from engine.harness.events import EventLog
from engine.harness.outcomes import (
    AnswerOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
    TurnResult,
)
from engine.harness.state import TurnState
from engine.tools.envelope import Table
from engine.verifier.models import (
    AttemptRecord,
    ClaimRecord,
    VerifierVerdict,
)


def test_event_log_emits_to_both_destinations():
    seen = []
    log = EventLog(listener=seen.append)
    log.emit("route", "start", "Consulting router (step 1)…")
    log.emit("tool:run_sql", "finish", "146 rows")

    # Destination one: the live listener, in order.
    assert [e.detail for e in seen] == ["Consulting router (step 1)…", "146 rows"]
    # Destination two: the trail that lands in turn_log.status_events.
    trail = json.loads(log.dump_json())
    assert [t["node"] for t in trail] == ["route", "tool:run_sql"]
    assert all(t["at"] for t in trail)


def test_event_log_without_listener_still_records():
    log = EventLog()
    log.emit("verify", "finish", "verified")
    assert log.events[0].phase == "finish"


def test_turn_outcome_union_round_trips():
    answer = AnswerOutcome(
        body=MarkdownAnswer(text="146 of 161."), verification="verified"
    )
    result = TurnResult(conversation_id=1, turn=1, outcome=answer)
    reread = TurnResult.model_validate_json(result.model_dump_json())
    assert reread == result
    assert reread.outcome.kind == "answer"
    assert reread.outcome.body.kind == "markdown"

    table = AnswerOutcome(
        body=TableAnswer(
            table=Table(columns=["n"], rows=[{"n": 146}], total_row_count=1),
            caption="SELECT ...",
        ),
        verification="verified",
    )
    refuse = RefuseOutcome(reason="out of scope", what_would_work="ask about data")
    for outcome in (table, refuse):
        wrapped = TurnResult(conversation_id=1, turn=2, outcome=outcome)
        assert TurnResult.model_validate_json(wrapped.model_dump_json()) == wrapped


def test_verifier_verdict_serializes_for_the_turn_log():
    verdict = VerifierVerdict(
        disposition="unverified",
        mode="prose",
        attempts=[
            AttemptRecord(
                attempt=1,
                claims=[
                    ClaimRecord(
                        kind="numeric",
                        surface="14,600",
                        start=10,
                        end=16,
                        status="unmatched",
                        reason="no evidence value matches",
                    )
                ],
                unmatched_count=1,
            )
        ],
        plausibility=[],
        judge_calls=1,
        reason="1 claim unsupported after retries",
    )
    assert VerifierVerdict.model_validate_json(verdict.model_dump_json()) == verdict


def test_turn_state_defaults_are_a_fresh_conversation():
    state = TurnState()
    assert state.history == [] and state.turn == 0
    assert state.evidence == [] and state.outcome is None
