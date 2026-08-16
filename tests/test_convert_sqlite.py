"""The SQLite -> DuckDB conversion step converts faithfully or not at all."""

import sqlite3

import duckdb
import pytest

from engine.packtools.convert_sqlite import ConversionError, convert

SHA = "761a18e9b9253870d930f1b13b3a852ce516d603"


def open_duckdb(path):
    return duckdb.connect(str(path), read_only=True)


def table_names(connection) -> list[str]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables ORDER BY table_name"
    ).fetchall()
    return [row[0] for row in rows]


def test_all_tables_arrive(snapshot_duckdb):
    with open_duckdb(snapshot_duckdb) as connection:
        assert len(table_names(connection)) == 14
        assert "invoices" in table_names(connection)


def test_rows_and_values_survive(snapshot_sqlite, snapshot_duckdb):
    source = sqlite3.connect(str(snapshot_sqlite))
    source.row_factory = sqlite3.Row
    with open_duckdb(snapshot_duckdb) as target:
        for table in ("suppliers", "invoices", "findings"):
            source_count = source.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]
            target_count = target.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            assert source_count == target_count, table
        # Spot-check one row by name-keyed values.
        source_row = source.execute(
            "SELECT invoice_number, invoice_total, adjustment_flag "
            "FROM invoices WHERE id = 1"
        ).fetchone()
        columns = ["invoice_number", "invoice_total", "adjustment_flag"]
        target_row = dict(
            zip(
                columns,
                target.execute(
                    "SELECT invoice_number, invoice_total, adjustment_flag "
                    "FROM invoices WHERE id = 1"
                ).fetchone(),
            )
        )
        assert target_row["invoice_number"] == source_row["invoice_number"]
        assert target_row["invoice_total"] == source_row["invoice_total"]
        # SQLite stores booleans as 0/1; the converter must deliver real ones.
        assert isinstance(target_row["adjustment_flag"], bool)
    source.close()


def test_types_translate(snapshot_duckdb):
    with open_duckdb(snapshot_duckdb) as connection:
        types = dict(
            connection.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'invoices'"
            ).fetchall()
        )
    assert types["id"] == "BIGINT"
    assert types["invoice_total"] == "DOUBLE"
    assert types["adjustment_flag"] == "BOOLEAN"
    assert types["received_at"] == "TIMESTAMP"
    assert types["status"] == "VARCHAR"


def test_constraints_preserved(snapshot_duckdb):
    with open_duckdb(snapshot_duckdb) as connection:
        constraints = connection.execute(
            "SELECT constraint_type, constraint_column_names "
            "FROM duckdb_constraints() WHERE table_name = 'invoices'"
        ).fetchall()
    by_type: dict[str, list] = {}
    for constraint_type, columns in constraints:
        by_type.setdefault(constraint_type, []).append(list(columns))
    assert [["id"]] == by_type["PRIMARY KEY"]
    assert [
        "supplier_id",
        "invoice_number",
        "revision",
    ] in by_type["UNIQUE"]
    assert "FOREIGN KEY" in by_type


def test_check_constraints_carried_when_present(tmp_path):
    """InvoiceGuard has none, but targets that do keep theirs."""
    sqlite_path = tmp_path / "with_check.db"
    connection = sqlite3.connect(str(sqlite_path))
    connection.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, "
        "status VARCHAR(10) CHECK (status IN ('OPEN', 'DONE')));\n"
        "INSERT INTO t (id, status) VALUES (1, 'OPEN');\n"
    )
    connection.commit()
    connection.close()
    convert(
        sqlite_path,
        tmp_path / "out.duckdb",
        source_commit_sha=None,
        simulation_seed=None,
    )
    with open_duckdb(tmp_path / "out.duckdb") as target:
        checks = target.execute(
            "SELECT expression FROM duckdb_constraints() "
            "WHERE table_name = 't' AND constraint_type = 'CHECK'"
        ).fetchall()
    assert checks and "OPEN" in checks[0][0]


def test_convert_twice_is_deterministic(snapshot_sqlite, tmp_path):
    results = []
    for name in ("a.duckdb", "b.duckdb"):
        manifest = convert(
            snapshot_sqlite,
            tmp_path / name,
            source_commit_sha=SHA,
            simulation_seed=42,
        )
        with open_duckdb(tmp_path / name) as connection:
            dump = []
            for table in table_names(connection):
                dump.append(
                    connection.execute(
                        f"SELECT * FROM {table} ORDER BY ALL"
                    ).fetchall()
                )
            results.append((manifest.manifest_id, dump))
    assert results[0] == results[1]


def test_manifest_records_the_pinning_pair(snapshot_sqlite, tmp_path):
    manifest = convert(
        snapshot_sqlite,
        tmp_path / "out.duckdb",
        source_commit_sha=SHA,
        simulation_seed=42,
    )
    assert manifest.generator == "sqlite_convert"
    assert manifest.source_commit_sha == SHA
    assert manifest.simulation_seed == 42
    assert len(manifest.source_tables) == 14


def test_unknown_type_refuses_loudly(tmp_path):
    sqlite_path = tmp_path / "weird.db"
    connection = sqlite3.connect(str(sqlite_path))
    connection.execute("CREATE TABLE t (x GEOMETRY)")
    connection.commit()
    connection.close()
    with pytest.raises(ConversionError, match="GEOMETRY"):
        convert(
            sqlite_path,
            tmp_path / "out.duckdb",
            source_commit_sha=None,
            simulation_seed=None,
        )


def test_missing_database_refuses_legibly(tmp_path):
    with pytest.raises(ConversionError, match="does not exist"):
        convert(
            tmp_path / "absent.db",
            tmp_path / "out.duckdb",
            source_commit_sha=None,
            simulation_seed=None,
        )


def test_converted_db_opens_through_phase1_adapter(snapshot_duckdb):
    """The pack's DuckDB file must be servable by the existing SqlPort
    adapter, read-only — the Phase 2 'done' criterion."""
    from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
    from engine.ports.types import User

    adapter = DuckDbSql(DuckDbSettings(database=str(snapshot_duckdb)))
    rows = adapter.run_sql(
        "SELECT COUNT(*) AS n FROM invoices",
        User(username="t", display_name="T"),
    )
    assert rows == [{"n": 50}]
