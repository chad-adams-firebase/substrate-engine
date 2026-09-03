"""Univariate Stats generator (Brief §4.9): per-column facts via SqlPort.

Fully machine-derived (no overlay). Every number is computed by SQL
and rounded at generation time, so identical data always serializes
to identical bytes. min/max are rendered as text uniformly — a
timestamp's minimum travels the same way a price's does.
"""

from datetime import date, datetime

from engine.config.models import GenerationConfig
from engine.ports.sql import SqlPort
from engine.ports.types import User
from engine.substrates.manifest import build_manifest
from engine.substrates.models import (
    STATS_DECIMALS,
    Manifest,
    Provenance,
    StatsRow,
    TopValue,
)

GENERATOR_VERSION = "1.0.0"

NUMERIC_TYPES = {"BIGINT", "DOUBLE", "INTEGER", "FLOAT", "DECIMAL"}
TOP_VALUES_LIMIT = 5
# Rendered values are display material; unbounded payload blobs would
# bloat the substrate without informing anyone.
RENDER_MAX_CHARS = 120


def _render(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        value = round(value, STATS_DECIMALS)
    return str(value)[:RENDER_MAX_CHARS]


class StatsGenerator:
    def __init__(
        self, sql: SqlPort, identity: User, config: GenerationConfig
    ) -> None:
        self._sql = sql
        self._identity = identity
        self._config = config

    def generate(
        self, *, source_commit_sha: str | None
    ) -> tuple[list[StatsRow], Manifest]:
        columns = self._run(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema = 'main' "
            "ORDER BY table_name, column_name"
        )
        tables = sorted({row["table_name"] for row in columns})
        manifest = build_manifest(
            "stats",
            GENERATOR_VERSION,
            source_commit_sha=source_commit_sha,
            simulation_seed=self._config.simulation_seed,
            source_tables=tables,
        )
        provenance = Provenance(
            source="machine",
            confidence=1.0,
            needs_validation=False,
            manifest_id=manifest.manifest_id,
        )

        row_counts = {
            table: self._run(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
            for table in tables
        }

        rows = [
            self._column_stats(column, row_counts, provenance)
            for column in columns
        ]
        return rows, manifest

    def _run(self, query: str) -> list[dict]:
        return self._sql.run_sql(query, self._identity)

    def _column_stats(
        self, column: dict, row_counts: dict[str, int], provenance: Provenance
    ) -> StatsRow:
        table, name, data_type = (
            column["table_name"],
            column["column_name"],
            column["data_type"],
        )
        quoted = f'"{name}"'
        row_count = row_counts[table]

        base = self._run(
            f"SELECT COUNT({quoted}) AS non_null, "
            f"COUNT(DISTINCT {quoted}) AS distinct_count FROM \"{table}\""
        )[0]
        null_rate = (
            round((row_count - base["non_null"]) / row_count, STATS_DECIMALS)
            if row_count
            else 0.0
        )

        min_value = max_value = None
        mean = None
        if row_count and base["non_null"]:
            extremes = self._run(
                f"SELECT MIN({quoted}) AS lo, MAX({quoted}) AS hi FROM \"{table}\""
            )[0]
            min_value = _render(extremes["lo"])
            max_value = _render(extremes["hi"])
            if data_type in NUMERIC_TYPES:
                mean_row = self._run(
                    f"SELECT AVG({quoted}) AS mean FROM \"{table}\""
                )[0]
                mean = round(float(mean_row["mean"]), STATS_DECIMALS)

        top_values = []
        if base["non_null"]:
            top_rows = self._run(
                f"SELECT {quoted} AS value, COUNT(*) AS n FROM \"{table}\" "
                f"WHERE {quoted} IS NOT NULL "
                f"GROUP BY value ORDER BY n DESC, value ASC "
                f"LIMIT {TOP_VALUES_LIMIT}"
            )
            top_values = [
                TopValue(value=_render(row["value"]), count=row["n"])
                for row in top_rows
            ]

        return StatsRow(
            table_name=table,
            column_name=name,
            data_type=data_type,
            row_count=row_count,
            null_rate=null_rate,
            distinct_count=base["distinct_count"],
            min_value=min_value,
            max_value=max_value,
            mean=mean,
            top_values=top_values,
            provenance=provenance,
        )
