"""Shared builders for Verifier tests: hand-built invocations and a
verifier wired to the scripted LLM."""

from engine.config.models import VerifierSettings
from engine.substrates.models import Provenance, StatsRow
from engine.tools.envelope import (
    RunSqlEvidence,
    RunSqlOutput,
    SqlAttempt,
    Table,
    ToolInvocation,
)
from engine.verifier.checks import CheckRegistry, default_checks
from engine.verifier.verify import Verifier
from tests.stubs.llm_stub import ScriptedLLM

MACHINE = Provenance(
    source="machine", confidence=1.0, needs_validation=False, manifest_id="m1"
)


def stats_row(table: str, column: str, **overrides) -> StatsRow:
    fields = {
        "table_name": table,
        "column_name": column,
        "data_type": "INTEGER",
        "row_count": 161,
        "null_rate": 0.0,
        "distinct_count": 100,
        "provenance": MACHINE,
    }
    fields.update(overrides)
    return StatsRow(**fields)


def sql_invocation(
    sql: str,
    rows: list[dict],
    *,
    total: int | None = None,
    truncated=False,
    final_lint: str | None = None,
    final_enum_lint: str | None = None,
    final_interval_lint: str | None = None,
    column_formats: dict | None = None,
) -> ToolInvocation:
    columns = list(rows[0].keys()) if rows else []
    evidence = None
    lints = (final_lint, final_enum_lint, final_interval_lint)
    if any(reason is not None for reason in lints):
        # The overridden-challenge shape: the executed (final, clean-
        # error) attempt still carries the lint reason(s).
        error = " ".join(r for r in lints if r)
        evidence = RunSqlEvidence(
            grounding_prompt="",
            attempts=[
                SqlAttempt(raw_response=sql, sql=sql, error=error,
                           lint=final_lint, enum_lint=final_enum_lint,
                           interval_lint=final_interval_lint),
                SqlAttempt(raw_response=sql, sql=sql, row_count=len(rows),
                           lint=final_lint, enum_lint=final_enum_lint,
                           interval_lint=final_interval_lint),
            ],
        )
    return ToolInvocation(
        tool="run_sql",
        arguments={"question": "q"},
        status="ok",
        output=RunSqlOutput(
            sql=sql,
            table=Table(
                columns=columns,
                rows=rows,
                total_row_count=total if total is not None else len(rows),
                truncated=truncated,
                column_formats=column_formats or {},
            ),
        ),
        evidence=evidence,
        substrates_read=[],
    )


def make_verifier(
    llm_responses=None,
    settings: VerifierSettings | None = None,
    stats: list[StatsRow] | None = None,
) -> tuple[Verifier, ScriptedLLM]:
    llm = ScriptedLLM(llm_responses or [])
    verifier = Verifier(
        CheckRegistry(default_checks()),
        llm,
        settings or VerifierSettings(),
        lambda: stats or [],
    )
    return verifier, llm


# The post-coverage bank's W3 rep 4 (2026-09-02), verbatim: the
# subtraction in a CTE, the AVG and the scaling outside. Both are
# INTERVALs; the result was 0:00:00.041667, humanized "0 seconds",
# verified. Shared by the lint tests and the plausibility tests.
W3_REP4_SQL = """WITH received_to_ready AS (
    SELECT 
        r.invoice_id,
        r.at - rr.at AS time_in_seconds
    FROM 
        invoice_history rr
    JOIN 
        invoice_history r
    ON 
        rr.invoice_id = r.invoice_id
    WHERE 
        rr.to_status = 'RECEIVED' 
        AND r.from_status = 'RECEIVED' 
        AND r.to_status = 'READY'
)
SELECT 
    AVG(time_in_seconds) / 86400 AS avg_time_in_days
FROM 
    received_to_ready"""
