"""The envelope contract: serialize → deserialize → identical.

This is the Phase 4 no-retrofit guarantee. The Verifier and turn_log
consume persisted TurnEvidence; if an envelope cannot round-trip
through its canonical JSON with nothing lost, evidence_bundle_ref
becomes a lie. Every discriminated-union member must reconstruct as
its own type with no external context.
"""

from engine.config.models import SubstrateName, ToolName
from engine.ports.types import RunStatus, UnitSummary
from engine.substrates.models import Provenance, StatsRow
from engine.tools.envelope import (
    CheckExecutionEvidence,
    CheckExecutionOutput,
    RunSqlEvidence,
    RunSqlOutput,
    SqlAttempt,
    StatsOutput,
    Table,
    ToolInvocation,
    dumps_turn_evidence,
    loads_turn_evidence,
)

MACHINE = Provenance(
    source="machine", confidence=1.0, needs_validation=True, manifest_id="abc123"
)


def _stats_invocation() -> ToolInvocation:
    return ToolInvocation(
        tool=ToolName.QUERY_UNIVARIATE_STATS,
        arguments={"table": "invoices", "column": "status"},
        status="ok",
        output=StatsOutput(
            rows=[
                StatsRow(
                    table_name="invoices",
                    column_name="status",
                    data_type="VARCHAR",
                    row_count=1990,
                    null_rate=0.0,
                    distinct_count=4,
                    provenance=MACHINE,
                )
            ]
        ),
        substrates_read=[SubstrateName.UNIVARIATE_STATISTICS],
        manifest_ids=["abc123"],
    )


def _run_sql_invocation() -> ToolInvocation:
    return ToolInvocation(
        tool=ToolName.RUN_SQL,
        arguments={"question": "how many invoices?"},
        status="ok",
        output=RunSqlOutput(
            sql="SELECT COUNT(*) AS n FROM invoices",
            table=Table(columns=["n"], rows=[{"n": 1990}], total_row_count=1),
        ),
        evidence=RunSqlEvidence(
            grounding_prompt="You are grounded.",
            attempts=[
                SqlAttempt(
                    raw_response="```sql\nSELECT nope\n```",
                    sql="SELECT nope",
                    error='Binder Error: column "nope" not found',
                ),
                SqlAttempt(
                    raw_response="SELECT COUNT(*) AS n FROM invoices",
                    sql="SELECT COUNT(*) AS n FROM invoices",
                    row_count=1,
                ),
            ],
        ),
        substrates_read=[
            SubstrateName.DATA_DICTIONARY,
            SubstrateName.DATA_DICTIONARY_MAP,
            SubstrateName.APPLICATION_DATABASE,
        ],
    )


def _error_invocation() -> ToolInvocation:
    return ToolInvocation(
        tool=ToolName.CHECK_EXECUTION,
        arguments={"component": "no_such_component", "mode": "did_run"},
        status="error",
        error="Unknown component 'no_such_component'. Known: stale_sweep.",
    )


def _check_execution_invocation() -> ToolInvocation:
    return ToolInvocation(
        tool=ToolName.CHECK_EXECUTION,
        arguments={"component": "stale_sweep", "mode": "did_run"},
        status="ok",
        output=CheckExecutionOutput(
            run_status=RunStatus(
                ran=True, count=2, detail="2 stale_sweep_completed events"
            )
        ),
        evidence=CheckExecutionEvidence(
            lines=["ts=2026-03-11T18:00:00+00:00 level=INFO ..."]
        ),
        substrates_read=[SubstrateName.APPLICATION_LOGS],
    )


def test_recent_errors_output_round_trips_with_error_count():
    """Fix pass 4 (gate verdict N12): the recent_errors scalar mirror
    of run_status.count survives the codec byte-stably."""
    turn = [
        ToolInvocation(
            tool=ToolName.CHECK_EXECUTION,
            arguments={"component": "benchmark_scoring", "mode": "recent_errors"},
            status="ok",
            output=CheckExecutionOutput(
                errors=[{"ts": "2026-03-11T08:00:00+00:00", "event": "x"}],
                error_count=1,
            ),
            evidence=CheckExecutionEvidence(lines=["raw line"]),
            substrates_read=[SubstrateName.APPLICATION_LOGS],
        )
    ]
    text = dumps_turn_evidence(turn)
    restored = loads_turn_evidence(text)
    assert restored == turn
    assert restored[0].output.error_count == 1
    assert dumps_turn_evidence(restored) == text


def test_turn_evidence_round_trips_identically():
    turn = [
        _stats_invocation(),
        _run_sql_invocation(),
        _error_invocation(),
        _check_execution_invocation(),
    ]
    text = dumps_turn_evidence(turn)
    restored = loads_turn_evidence(text)
    assert restored == turn
    # And the canonical bytes are stable: dumping the restored turn
    # reproduces them exactly.
    assert dumps_turn_evidence(restored) == text


def test_union_members_restore_as_their_own_types():
    text = dumps_turn_evidence([_run_sql_invocation()])
    [restored] = loads_turn_evidence(text)
    assert isinstance(restored.output, RunSqlOutput)
    assert isinstance(restored.evidence, RunSqlEvidence)
    assert restored.output.table.rows == [{"n": 1990}]


def test_error_invocation_carries_no_output():
    [restored] = loads_turn_evidence(dumps_turn_evidence([_error_invocation()]))
    assert restored.status == "error"
    assert restored.output is None
    assert restored.error is not None


def test_known_items_output_round_trips():
    from engine.tools.envelope import KnownItemsOutput

    invocation = ToolInvocation(
        tool=ToolName.ANSWER_FROM_KNOWN_ITEMS,
        arguments={"query": "flag rate"},
        status="ok",
        output=KnownItemsOutput(
            matches=[
                UnitSummary(
                    id=1,
                    title="Flag rates by supplier",
                    state="published",
                    author="dev",
                )
            ]
        ),
    )
    [restored] = loads_turn_evidence(dumps_turn_evidence([invocation]))
    assert restored == invocation


def test_run_sql_readings_round_trip_and_default_when_absent():
    """Close Pass: readings ride on the run_sql output; a bundle
    written before the field loads with none (extra="forbid" rejects
    unknown keys, not absent ones — old reports stay readable)."""
    import json

    from engine.substrates.models import Interpretation

    invocation = _run_sql_invocation()
    invocation.output.readings = [
        Interpretation(name="closed-invoice opportunity", meaning="the rollup")
    ]
    text = dumps_turn_evidence([invocation])
    (restored,) = loads_turn_evidence(text)
    assert restored.output.readings[0].name == "closed-invoice opportunity"
    assert restored == invocation

    legacy = json.loads(text)
    del legacy[0]["output"]["readings"]
    (older,) = loads_turn_evidence(json.dumps(legacy))
    assert older.output.readings == []
