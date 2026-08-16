"""Deterministic SQLite -> DuckDB conversion (pack-build step).

Why this exists (decision recorded in Brief §18, do not relitigate):
DuckDB's sqlite_scanner extension downloads at runtime, which breaks
offline-green tests and dies behind the corporate proxy. Instead this
utility reads the target application's SQLite database via stdlib
sqlite3 and writes the pack's DuckDB file, emitting a manifest like
any generator.

Adapter-category code, deliberately: it imports sqlite3 and duckdb
directly because it manufactures the artifact the DuckDB SqlPort
adapter serves. It is exempted by name in tests/test_architecture.py
for exactly that reason. It still reads rows by NAME, never position
(CLAUDE.md data law).

Structure is taken from PRAGMAs, not by parsing CREATE TABLE text —
except CHECK constraints, which SQLite only exposes as DDL text and
are carried over verbatim when present (the dictionary generator's
constraint-based enum detection consumes them on targets that have
them; SQLAlchemy's native_enum=False default emits none).
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import duckdb

from engine.substrates.manifest import build_manifest
from engine.substrates.models import Manifest

GENERATOR_VERSION = "1.0.0"

# SQLite declared types -> DuckDB types. Declared types arrive with
# length suffixes stripped ("VARCHAR(100)" -> "VARCHAR"). Anything
# unlisted raises: silently guessing a type would poison every
# downstream substrate.
TYPE_MAP = {
    "INTEGER": "BIGINT",
    "BIGINT": "BIGINT",
    "VARCHAR": "VARCHAR",
    "TEXT": "VARCHAR",
    "FLOAT": "DOUBLE",
    "REAL": "DOUBLE",
    "DOUBLE": "DOUBLE",
    "BOOLEAN": "BOOLEAN",
    "DATETIME": "TIMESTAMP",
    "DATE": "DATE",
    "JSON": "VARCHAR",
    "BLOB": "BLOB",
    "NUMERIC": "DOUBLE",
}


class ConversionError(Exception):
    """The database cannot be converted faithfully. The message names
    the table/column/value so the pack author can act on it."""


def _quote(identifier: str) -> str:
    """Always-quoted identifiers: the target app owns its names, and
    some ("at") collide with DuckDB keywords."""
    return '"' + identifier.replace('"', '""') + '"'


def _duckdb_type(declared: str, table: str, column: str) -> str:
    base = declared.split("(")[0].strip().upper()
    if base not in TYPE_MAP:
        raise ConversionError(
            f"{table}.{column}: no DuckDB mapping for SQLite type "
            f"{declared!r} — extend TYPE_MAP deliberately."
        )
    return TYPE_MAP[base]


def _check_clauses(create_sql: str) -> list[str]:
    """Extract CHECK (...) clause bodies from CREATE TABLE text with a
    paren-balanced scan (regexes cannot pair nested parentheses)."""
    clauses = []
    for match in re.finditer(r"\bCHECK\s*\(", create_sql, re.IGNORECASE):
        depth = 1
        position = match.end()
        while position < len(create_sql) and depth > 0:
            if create_sql[position] == "(":
                depth += 1
            elif create_sql[position] == ")":
                depth -= 1
            position += 1
        if depth == 0:
            clauses.append(create_sql[match.end():position - 1].strip())
    return clauses


def _table_order(
    tables: list[str], foreign_keys: dict[str, set[str]]
) -> list[str]:
    """Parents before children so DuckDB's FK enforcement accepts the
    inserts. Self-references are fine when rows arrive in primary-key
    order (a row's parent has a lower id); a genuine cycle between
    tables cannot be ordered and is reported, not guessed at."""
    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(
            table
            for table in remaining
            if not (foreign_keys.get(table, set()) & remaining - {table})
        )
        if not ready:
            raise ConversionError(
                f"cyclic foreign keys between tables {sorted(remaining)} — "
                f"cannot order inserts; convert this target manually."
            )
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def _coerce(value: object, duck_type: str, table: str, column: str) -> object:
    if value is None:
        return None
    if duck_type == "BOOLEAN":
        if value in (0, 1):
            return bool(value)
        raise ConversionError(f"{table}.{column}: non-boolean value {value!r}")
    if duck_type == "TIMESTAMP":
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ConversionError(
                f"{table}.{column}: unparseable timestamp {value!r}"
            ) from exc
    return value


def convert(
    sqlite_path: Path,
    duckdb_path: Path,
    *,
    source_commit_sha: str | None,
    simulation_seed: int | None,
) -> Manifest:
    """Convert the whole SQLite database into a fresh DuckDB file.

    Overwrites duckdb_path (the pack's database is a regenerated
    artifact, never hand-tended). Returns the sqlite_convert manifest;
    the caller decides where to store it.
    """
    if not sqlite_path.is_file():
        raise ConversionError(f"SQLite database does not exist: {sqlite_path}")
    if duckdb_path.exists():
        duckdb_path.unlink()

    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row
    try:
        tables = [
            row["name"]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

        columns_by_table: dict[str, list[sqlite3.Row]] = {}
        types_by_table: dict[str, dict[str, str]] = {}
        ddl_by_table: dict[str, str] = {}
        fk_parents: dict[str, set[str]] = {}

        for table in tables:
            info = source.execute(f"PRAGMA table_info({table})").fetchall()
            columns_by_table[table] = info
            types_by_table[table] = {
                row["name"]: _duckdb_type(row["type"], table, row["name"])
                for row in info
            }

            pk_columns = [
                row["name"]
                for row in sorted(info, key=lambda r: r["pk"])
                if row["pk"] > 0
            ]
            pieces = [
                f'{_quote(row["name"])} {types_by_table[table][row["name"]]}'
                + (" NOT NULL" if row["notnull"] and not row["pk"] else "")
                for row in info
            ]
            if pk_columns:
                quoted = ", ".join(_quote(name) for name in pk_columns)
                pieces.append(f"PRIMARY KEY ({quoted})")

            for index in source.execute(f"PRAGMA index_list({table})"):
                if index["origin"] == "u":  # UNIQUE constraints, not plain indexes
                    unique_columns = [
                        _quote(row["name"])
                        for row in source.execute(
                            f"PRAGMA index_info({index['name']})"
                        )
                    ]
                    pieces.append(f"UNIQUE ({', '.join(unique_columns)})")

            fk_rows = source.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            fk_parents[table] = {row["table"] for row in fk_rows}
            by_id: dict[int, list[sqlite3.Row]] = {}
            for row in fk_rows:
                by_id.setdefault(row["id"], []).append(row)
            for group in by_id.values():
                ordered = sorted(group, key=lambda r: r["seq"])
                local = ", ".join(_quote(row["from"]) for row in ordered)
                remote = ", ".join(_quote(row["to"]) for row in ordered)
                target = _quote(ordered[0]["table"])
                pieces.append(
                    f"FOREIGN KEY ({local}) REFERENCES {target} ({remote})"
                )

            create_sql = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            ).fetchone()["sql"]
            for clause in _check_clauses(create_sql):
                pieces.append(f"CHECK ({clause})")

            ddl_by_table[table] = (
                f"CREATE TABLE {_quote(table)} (\n  "
                + ",\n  ".join(pieces)
                + "\n)"
            )

        ordered_tables = _table_order(tables, fk_parents)

        duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        target = duckdb.connect(str(duckdb_path))
        try:
            for table in ordered_tables:
                target.execute(ddl_by_table[table])
            for table in ordered_tables:
                info = columns_by_table[table]
                names = [row["name"] for row in info]
                pk_columns = [row["name"] for row in info if row["pk"] > 0]
                order_by = ", ".join(pk_columns) if pk_columns else "rowid"
                placeholders = ", ".join("?" for _ in names)
                quoted_names = ", ".join(_quote(name) for name in names)
                insert = (
                    f"INSERT INTO {_quote(table)} ({quoted_names}) "
                    f"VALUES ({placeholders})"
                )
                for row in source.execute(
                    f"SELECT * FROM {table} ORDER BY {order_by}"
                ):
                    values = [
                        _coerce(row[name], types_by_table[table][name], table, name)
                        for name in names
                    ]
                    target.execute(insert, values)
        finally:
            target.close()
    finally:
        source.close()

    return build_manifest(
        "sqlite_convert",
        GENERATOR_VERSION,
        source_commit_sha=source_commit_sha,
        simulation_seed=simulation_seed,
        source_tables=tables,
    )
