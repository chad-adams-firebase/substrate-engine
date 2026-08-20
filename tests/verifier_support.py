"""Shared builders for Verifier tests: hand-built invocations and a
verifier wired to the scripted LLM."""

from engine.config.models import VerifierSettings
from engine.substrates.models import Provenance, StatsRow
from engine.tools.envelope import RunSqlOutput, Table, ToolInvocation
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
    sql: str, rows: list[dict], *, total: int | None = None, truncated=False
) -> ToolInvocation:
    columns = list(rows[0].keys()) if rows else []
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
            ),
        ),
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
