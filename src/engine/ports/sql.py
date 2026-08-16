"""SqlPort — read queries against the target application's database.

Local adapter: DuckDB seeded by the pack. Real adapter (later phase):
databricks-sql-connector forwarding the user's token.

Rows are name-keyed dicts, never tuples: positional column access is
banned repo-wide (a real production bug motivates this — CLAUDE.md).
Join-key normalization (lowercasing) is this adapter boundary's
responsibility and happens nowhere else; the normalization config
itself arrives with the pack substrates in Phase 2.
"""

from typing import Any, Protocol

from engine.ports.types import User


class SqlPort(Protocol):
    def run_sql(self, query: str, identity: User) -> list[dict[str, Any]]: ...
