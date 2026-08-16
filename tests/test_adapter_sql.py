"""DuckDB SqlPort adapter: rows come back as name-keyed dicts, and a
missing database file is a legible error."""

from decimal import Decimal

import duckdb
import pytest

from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.ports.types import User

IDENTITY = User(username="tester", display_name="Test User")


@pytest.fixture
def seeded(tmp_path):
    """A small on-disk database, seeded directly (packs own seeding;
    the adapter itself only reads)."""
    path = str(tmp_path / "app.duckdb")
    connection = duckdb.connect(path)
    connection.execute(
        "CREATE TABLE invoices (invoice_id INTEGER, supplier TEXT, total DECIMAL(10,2))"
    )
    connection.execute(
        "INSERT INTO invoices VALUES (1, 'Acme', 120.50), (2, 'Globex', 99.99)"
    )
    connection.close()
    return DuckDbSql(DuckDbSettings(database=path))


def test_rows_are_name_keyed_dicts(seeded):
    rows = seeded.run_sql(
        "SELECT invoice_id, supplier, total FROM invoices ORDER BY invoice_id",
        IDENTITY,
    )

    # DECIMAL columns arrive as decimal.Decimal — values reach the
    # engine undegraded, which the Verifier's exact matching relies on.
    assert rows == [
        {"invoice_id": 1, "supplier": "Acme", "total": Decimal("120.50")},
        {"invoice_id": 2, "supplier": "Globex", "total": Decimal("99.99")},
    ]
    # Name-based access is the contract; positional access is impossible.
    assert rows[0]["supplier"] == "Acme"


def test_aggregates_are_name_keyed_via_alias(seeded):
    rows = seeded.run_sql(
        "SELECT COUNT(*) AS invoice_count FROM invoices", IDENTITY
    )

    assert rows == [{"invoice_count": 2}]


def test_file_database_is_read_only(seeded):
    """The engine only reads the target app's data."""
    with pytest.raises(duckdb.Error):
        seeded.run_sql("CREATE TABLE scratch (x INTEGER)", IDENTITY)


def test_missing_database_file_is_legible(tmp_path):
    adapter = DuckDbSql(DuckDbSettings(database=str(tmp_path / "absent.duckdb")))

    with pytest.raises(FileNotFoundError, match="packs seed"):
        adapter.run_sql("SELECT 1", IDENTITY)


def test_in_memory_database_for_tests():
    adapter = DuckDbSql(DuckDbSettings(database=":memory:"))

    assert adapter.run_sql("SELECT 1 AS one", IDENTITY) == [{"one": 1}]
