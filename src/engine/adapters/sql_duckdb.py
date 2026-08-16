"""DuckDB adapter for SqlPort.

DuckDB over SQLite because its SQL dialect sits much closer to the
Databricks SQL the real adapter will speak — date functions,
percentiles, casting — so NL->SQL behavior developed locally
transfers instead of being retuned (decision recorded in the Phase 1
plan).

Rows come back as name-keyed dicts, never tuples. Positional access
is structurally impossible from here on out.
"""

from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict

from engine.ports.types import User


class DuckDbSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Path to the pack-seeded database, relative to the pack directory
    # (resolved by the container), or ":memory:" for tests.
    database: str


class DuckDbSql:
    def __init__(self, settings: DuckDbSettings) -> None:
        self._settings = settings
        self._connection: duckdb.DuckDBPyConnection | None = None

    @property
    def settings(self) -> DuckDbSettings:
        return self._settings

    def run_sql(self, query: str, identity: User) -> list[dict[str, Any]]:
        # identity is unused locally; the real databricks-sql-connector
        # adapter forwards the user's token. The parameter stays so both
        # adapters present the identical surface.
        cursor = self._connect().execute(query)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            database = self._settings.database
            if database != ":memory:" and not Path(database).exists():
                raise FileNotFoundError(
                    f"DuckDB database does not exist: {database} — packs seed "
                    f"their database before the engine reads it."
                )
            # read_only would be ideal (the engine only reads the target
            # app's data), but read_only requires an existing file and
            # cannot apply to :memory:; enforced for file databases only.
            self._connection = duckdb.connect(
                database, read_only=database != ":memory:"
            )
        return self._connection
