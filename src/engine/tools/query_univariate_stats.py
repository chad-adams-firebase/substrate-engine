"""query_univariate_stats — typed stats lookup by table, optionally
narrowed to one column."""

from pydantic import BaseModel, ConfigDict

from engine.config.models import SubstrateName, ToolName
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.envelope import StatsOutput, ToolInvocation


class StatsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str | None = None


class QueryUnivariateStats(Tool):
    name = ToolName.QUERY_UNIVARIATE_STATS
    description = (
        "Look up univariate statistics for a database table: per-column "
        "type, row count, null rate, distinct count, min/max, mean, and "
        "most frequent values. Optionally narrow to one column."
    )
    input_model = StatsInput

    def __init__(self, store: SubstrateStorePort) -> None:
        self._store = store

    def run(self, params: StatsInput) -> ToolInvocation:
        try:
            rows = self._store.stats()
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))
        matches = [row for row in rows if row.table_name == params.table]
        if not matches:
            known = ", ".join(sorted({row.table_name for row in rows}))
            return self.fail(
                params,
                f"No statistics for table {params.table!r}. Known tables: {known}.",
            )
        if params.column is not None:
            matches = [row for row in matches if row.column_name == params.column]
            if not matches:
                return self.fail(
                    params,
                    f"Table {params.table!r} has no column {params.column!r} "
                    f"in the statistics substrate.",
                )
        return self.ok(
            params,
            StatsOutput(rows=matches),
            substrates_read=[SubstrateName.UNIVARIATE_STATISTICS],
            manifest_ids=manifest_ids_of(matches),
        )
