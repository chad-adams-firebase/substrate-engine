"""Data Dictionary generator: schema introspection + SME overlay merge.

Produces the structural skeleton (tables, columns, types, keys, enum
candidates) from the pack's database via SqlPort, then merges the
human overlay per the §4 provenance rules: machine owns structure,
humans own meaning, and a rebuild can refresh the former without ever
touching the latter (the overlay lives in its own file the generator
only reads).

Enum detection, two paths:
- CHECK constraints (`col IN (...)`) when the schema has them —
  authoritative, enum_source="check_constraint".
- Otherwise a data-scan heuristic: a VARCHAR column whose distinct
  values are few (pack-config threshold) and enum-shaped. Honest
  about its nature: enum_source="data_scan", low confidence, always
  needs_validation. SQLAlchemy's native_enum=False default emits no
  CHECKs, so this is the working path for the reference target — and
  likely for the real one.
"""

import re

from engine.config.models import GenerationConfig
from engine.ports.sql import SqlPort
from engine.ports.types import User
from engine.substrates.manifest import build_manifest
from engine.substrates.models import DictionaryRow, Manifest, Provenance

GENERATOR_VERSION = "1.0.0"

ENUM_VALUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
# "(status IN ('OPEN', 'DONE'))" — the shape DuckDB reports for a
# carried-over SQLite enum CHECK.
CHECK_IN_PATTERN = re.compile(
    r"^\(?\s*\"?(?P<column>\w+)\"?\s+IN\s+\((?P<values>[^)]*)\)\s*\)?$",
    re.IGNORECASE,
)
def _machine(manifest_id: str, confidence: float = 1.0) -> Provenance:
    return Provenance(
        source="machine",
        confidence=confidence,
        needs_validation=True,
        manifest_id=manifest_id,
    )


class DictionaryGenerator:
    def __init__(
        self, sql: SqlPort, identity: User, config: GenerationConfig
    ) -> None:
        self._sql = sql
        self._identity = identity
        self._config = config

    def generate(
        self,
        overlay: list[DictionaryRow],
        *,
        source_commit_sha: str | None,
    ) -> tuple[list[DictionaryRow], Manifest, list[str]]:
        """Returns (merged rows, manifest, warnings). The caller owns
        writing files; the generator owns being right."""
        tables = self._table_names()
        manifest = build_manifest(
            "dictionary",
            GENERATOR_VERSION,
            source_commit_sha=source_commit_sha,
            simulation_seed=self._config.simulation_seed,
            source_tables=tables,
        )

        primary_keys, foreign_keys, check_enums = self._constraints()
        machine_rows: dict[tuple[str, str], DictionaryRow] = {}
        for table in tables:
            machine_rows[(table, "")] = DictionaryRow(
                table_name=table, provenance=_machine(manifest.manifest_id)
            )
            for column in self._columns(table):
                name = column["column_name"]
                enum_values, enum_source = check_enums.get((table, name), (None, None))
                if enum_values is None:
                    enum_values, enum_source = self._data_scan_enum(
                        table, name, column["data_type"]
                    )
                machine_rows[(table, name)] = DictionaryRow(
                    table_name=table,
                    column_name=name,
                    data_type=column["data_type"],
                    nullable=column["is_nullable"] == "YES",
                    is_primary_key=name in primary_keys.get(table, set()),
                    fk_target=foreign_keys.get((table, name)),
                    enum_values=enum_values,
                    enum_source=enum_source,
                    provenance=_machine(
                        manifest.manifest_id,
                        confidence=0.5 if enum_source == "data_scan" else 1.0,
                    ),
                )

        merged, warnings = _merge_overlay(machine_rows, overlay)
        return merged, manifest, warnings

    def _run(self, query: str) -> list[dict]:
        return self._sql.run_sql(query, self._identity)

    def _table_names(self) -> list[str]:
        rows = self._run(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        )
        return [row["table_name"] for row in rows]

    def _columns(self, table: str) -> list[dict]:
        return self._run(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            f"WHERE table_schema = 'main' AND table_name = '{table}' "
            "ORDER BY column_name"
        )

    def _constraints(
        self,
    ) -> tuple[
        dict[str, set[str]],
        dict[tuple[str, str], str],
        dict[tuple[str, str], tuple[list[str], str]],
    ]:
        primary_keys: dict[str, set[str]] = {}
        foreign_keys: dict[tuple[str, str], str] = {}
        check_enums: dict[tuple[str, str], tuple[list[str], str]] = {}
        rows = self._run(
            "SELECT table_name, constraint_type, expression, "
            "constraint_column_names, referenced_table, "
            "referenced_column_names "
            "FROM duckdb_constraints() ORDER BY table_name, constraint_index"
        )
        for row in rows:
            table = row["table_name"]
            if row["constraint_type"] == "PRIMARY KEY":
                primary_keys.setdefault(table, set()).update(
                    row["constraint_column_names"]
                )
            elif row["constraint_type"] == "FOREIGN KEY":
                # Structured columns, not constraint_text: DuckDB leaves
                # the text empty for self-referencing FKs.
                for local, remote in zip(
                    row["constraint_column_names"],
                    row["referenced_column_names"],
                ):
                    foreign_keys[(table, local)] = (
                        f"{row['referenced_table']}.{remote}"
                    )
            elif row["constraint_type"] == "CHECK":
                match = CHECK_IN_PATTERN.match((row["expression"] or "").strip())
                if not match:
                    continue
                values = sorted(
                    value.strip().strip("'") for value in match.group("values").split(",")
                )
                check_enums[(table, match.group("column"))] = (
                    values,
                    "check_constraint",
                )
        return primary_keys, foreign_keys, check_enums

    def _data_scan_enum(
        self, table: str, column: str, data_type: str
    ) -> tuple[list[str] | None, str | None]:
        if data_type != "VARCHAR":
            return None, None
        limit = self._config.enum_scan_max_distinct
        rows = self._run(
            f'SELECT DISTINCT "{column}" AS value FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL ORDER BY value LIMIT {limit + 1}'
        )
        values = [row["value"] for row in rows]
        if not values or len(values) > limit:
            return None, None
        if not all(ENUM_VALUE_PATTERN.match(value) for value in values):
            return None, None
        return values, "data_scan"


def _merge_overlay(
    machine_rows: dict[tuple[str, str], DictionaryRow],
    overlay: list[DictionaryRow],
) -> tuple[list[DictionaryRow], list[str]]:
    """Human rows are sacred (CLAUDE.md). A matched overlay row keeps
    the machine-refreshed structure but the human's description and
    provenance verbatim; an orphaned one (its column vanished) is
    preserved, flagged needs_validation, and warned about — silence is
    not an option for stale human knowledge."""
    merged = dict(machine_rows)
    warnings: list[str] = []
    for human in overlay:
        if human.provenance.source != "human":
            raise ValueError(
                f"overlay row {human.table_name}.{human.column_name or '(table)'} "
                f"is not source=human — overlays are the human layer only"
            )
        key = (human.table_name, human.column_name)
        machine = merged.get(key)
        if machine is None:
            warnings.append(
                f"overlay row {human.table_name}.{human.column_name or '(table)'} "
                f"matches no live table/column; preserved but needs validation"
            )
            merged[key] = human.model_copy(
                update={
                    "provenance": human.provenance.model_copy(
                        update={"needs_validation": True}
                    )
                }
            )
        else:
            merged[key] = machine.model_copy(
                update={
                    "description": human.description,
                    "provenance": human.provenance,
                }
            )
    return list(merged.values()), warnings
