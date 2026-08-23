"""Read-only handles on the target world, for gold scripts and grade.

The referee reaches the world independently: plain DuckDB reads, raw
log lines, the CKG JSONL — never the engine's tools. Gold computed
through the machinery under test would inherit its bugs; the grader
is subject to the faithfulness laws too (the grader's-correction rule,
docs/phase4-gate-closing-addendum.md §6). Importing duckdb here is
that independence, not an adapter bypass: World is the eval side's
own instrument, and nothing on an answer path touches it.

Where the world lives is still read from pack config — the single
source of truth for paths — but only the paths are read; no adapters
are built.
"""

import re
from pathlib import Path

import duckdb

from engine.config.models import PortName
from engine.config.pack_loader import load_pack
from engine.substrates.ckg_index import CkgIndex
from engine.substrates.jsonl import read_rows
from engine.substrates.models import (
    CkgConditional,
    CkgEdge,
    CkgNode,
    ComponentMembership,
)
from engine.substrates.pack_data import load_components


class WorldError(Exception):
    """The world is not where the config says it is; the message names
    the missing piece."""


class World:
    def __init__(
        self,
        *,
        duckdb_path: Path,
        log_path: Path | None = None,
        substrates_dir: Path | None = None,
        components_path: Path | None = None,
        docs_dir: Path | None = None,
        primer_path: Path | None = None,
    ) -> None:
        self.duckdb_path = duckdb_path
        self.log_path = log_path
        self.substrates_dir = substrates_dir
        self.components_path = components_path
        self.docs_dir = docs_dir
        self.primer_path = primer_path
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._ckg: CkgIndex | None = None

    @classmethod
    def from_pack(cls, pack_dir: str | Path) -> "World":
        pack = load_pack(pack_dir)

        sql_selection = pack.config.adapters.get(PortName.SQL)
        if sql_selection is None or "database" not in sql_selection.settings:
            raise WorldError(
                f"{pack.root}/config.yaml declares no sql database path."
            )
        log_selection = pack.config.adapters.get(PortName.EXECUTION_LOG)

        def resolve(value: str) -> Path:
            path = Path(value)
            return path if path.is_absolute() else pack.root / path

        def optional(path: Path) -> Path | None:
            return path if path.exists() else None

        return cls(
            duckdb_path=resolve(sql_selection.settings["database"]),
            log_path=(
                resolve(log_selection.settings["path"])
                if log_selection is not None
                and "path" in log_selection.settings
                else None
            ),
            substrates_dir=optional(pack.root / "substrates"),
            components_path=optional(pack.root / "components.yaml"),
            docs_dir=optional(pack.root / "business_docs"),
            primer_path=optional(pack.root / "primer.md"),
        )

    def sql(self, query: str) -> list[dict]:
        """Name-keyed rows (CLAUDE.md: never positional)."""
        if self._connection is None:
            if not self.duckdb_path.is_file():
                raise WorldError(
                    f"World database not found: {self.duckdb_path} — "
                    f"run `engine convert` first."
                )
            self._connection = duckdb.connect(
                str(self.duckdb_path), read_only=True
            )
        cursor = self._connection.execute(query)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def grep_log(self, pattern: str) -> list[str]:
        """Raw logfmt lines matching a regex — the same instrument the
        acceptance sessions used for execution gold."""
        if self.log_path is None or not self.log_path.is_file():
            raise WorldError(f"World log not found: {self.log_path}")
        compiled = re.compile(pattern)
        with self.log_path.open(encoding="utf-8") as handle:
            return [
                line.rstrip("\n") for line in handle if compiled.search(line)
            ]

    @property
    def ckg(self) -> CkgIndex:
        if self._ckg is None:
            if self.substrates_dir is None:
                raise WorldError("World has no substrates directory.")
            components = (
                load_components(self.components_path)
                if self.components_path is not None
                else []
            )
            self._ckg = CkgIndex(
                nodes=read_rows(
                    self.substrates_dir / "ckg_nodes.jsonl", CkgNode
                ),
                edges=read_rows(
                    self.substrates_dir / "ckg_edges.jsonl", CkgEdge
                ),
                conditionals=read_rows(
                    self.substrates_dir / "ckg_conditionals.jsonl",
                    CkgConditional,
                ),
                components=components,
                memberships=read_rows(
                    self.substrates_dir / "component_memberships.jsonl",
                    ComponentMembership,
                ),
            )
        return self._ckg
