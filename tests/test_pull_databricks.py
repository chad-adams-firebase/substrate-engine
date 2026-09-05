"""`engine pull` against a fake Statement Execution API (no network):
typed landing, versioned snapshots, keyset and offset paging, chunked
and slow results, the manifest pin, and every documented failure mode
with its hint."""

import copy

import duckdb
import pytest

from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.config.models import GenerationConfig, PullConfig
from engine.packtools import pull_databricks
from engine.packtools.pull_databricks import (
    PullError,
    base_url_for,
    plan_statements,
    pull,
)
from tests.fake_warehouse import FakeTable, FakeWarehouse

INVOICES = FakeTable(
    columns=[
        ("id", "LONG", "BIGINT"),
        ("supplier", "STRING", "STRING"),
        ("total", "DECIMAL", "DECIMAL(18,2)"),
        ("received_at", "TIMESTAMP", "TIMESTAMP"),
        ("flagged", "BOOLEAN", "BOOLEAN"),
        ("note", "STRING", "STRING"),
    ],
    rows=[
        [
            str(i),
            f"s{i % 3}",
            f"{i * 10}.25",
            f"2026-01-{(i % 28) + 1:02d}T10:00:00.000Z",
            "true" if i % 2 else "false",
            None if i % 5 == 0 else f"n{i}",
        ]
        for i in range(1, 251)
    ],
    version=17,
)
SUPPLIERS = FakeTable(  # a view: no Delta history
    columns=[("code", "STRING", "STRING"), ("name", "STRING", "STRING")],
    rows=[[f"s{i}", f"Supplier {i}"] for i in range(3)],
    version=None,
)


def make_config(**overrides) -> PullConfig:
    base = {
        "warehouse_id": "wh-1",
        "catalog": "cat",
        "schema": "sch",
        "tables": [
            {"name": "invoices", "key": "id"},
            {"name": "suppliers", "versioned": False},
        ],
        "page_rows": 100,
    }
    return PullConfig.model_validate({**base, **overrides})


@pytest.fixture
def warehouse(monkeypatch):
    fake = FakeWarehouse(
        {"invoices": copy.deepcopy(INVOICES), "suppliers": copy.deepcopy(SUPPLIERS)},
        chunk_rows=40,
    )
    monkeypatch.setattr(pull_databricks, "_client_factory", fake.client_factory)
    monkeypatch.setattr(pull_databricks, "POLL_SECONDS", 0)
    return fake


def _query(path, sql):
    connection = duckdb.connect(str(path), read_only=True)
    try:
        cursor = connection.execute(sql)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def test_pull_lands_typed_tables_and_a_pinned_manifest(warehouse, tmp_path):
    path = tmp_path / "app.duckdb"
    outcome = pull(make_config(), path, host="example.test", token="dapi-test")

    assert [(t.name, t.rows, t.version) for t in outcome.tables] == [
        ("invoices", 250, 17),
        ("suppliers", 3, None),
    ]
    types = {
        row["column_name"]: row["data_type"]
        for row in _query(
            path,
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'invoices'",
        )
    }
    assert types == {
        "id": "BIGINT",
        "supplier": "VARCHAR",
        "total": "DOUBLE",  # DECIMAL lands as DOUBLE: the stats generator reads DOUBLE
        "received_at": "TIMESTAMP",
        "flagged": "BOOLEAN",
        "note": "VARCHAR",
    }
    (five,) = _query(path, "SELECT * FROM invoices WHERE id = 5")
    assert five["total"] == 50.25 and five["flagged"] is True and five["note"] is None
    assert str(five["received_at"]).startswith("2026-01-06 10:00:00")
    (nulls,) = _query(path, "SELECT COUNT(*) AS n FROM invoices WHERE note IS NULL")
    assert nulls["n"] == 50
    assert _query(path, "SELECT COUNT(*) AS n FROM suppliers")[0]["n"] == 3

    manifest = outcome.manifest
    assert manifest.generator == "databricks_pull"
    assert manifest.source_tables == ["invoices", "suppliers"]
    assert manifest.source_snapshot == "cat.sch|invoices@17,suppliers@unversioned"
    assert manifest.source_commit_sha is None and manifest.simulation_seed is None

    # Keyset paging under one snapshot for the keyed table; offset
    # paging for the keyless one.
    pages = [s for s in warehouse.statements if s.startswith("SELECT *") and "`invoices`" in s]
    assert len(pages) == 3
    assert all("VERSION AS OF 17" in s and "ORDER BY `id`" in s for s in pages)
    assert ":last_key" not in pages[0] and ":last_key" in pages[1]
    assert any("`suppliers`" in s and "ORDER BY ALL" in s and "OFFSET 0" in s for s in warehouse.statements)
    assert not any("DESCRIBE HISTORY" in s and "`suppliers`" in s for s in warehouse.statements)


def test_chunked_and_slow_results_are_followed_to_the_end(warehouse, tmp_path):
    """A 100-row page arrives in three chunks, after two RUNNING polls."""
    warehouse.pending_polls = 2
    path = tmp_path / "app.duckdb"
    outcome = pull(make_config(), path, host="example.test", token="dapi-test")
    assert outcome.tables[0].rows == 250
    assert _query(path, "SELECT COUNT(*) AS n FROM invoices")[0]["n"] == 250


def test_manifest_id_is_stable_at_the_same_versions_and_moves_with_them(warehouse, tmp_path):
    first = pull(make_config(), tmp_path / "a.duckdb", host="h", token="dapi-test")
    second = pull(make_config(), tmp_path / "b.duckdb", host="h", token="dapi-test")
    assert first.manifest.manifest_id == second.manifest.manifest_id

    warehouse.tables["invoices"].version = 18
    moved = pull(make_config(), tmp_path / "c.duckdb", host="h", token="dapi-test")
    assert moved.manifest.manifest_id != first.manifest.manifest_id
    assert moved.manifest.source_snapshot == "cat.sch|invoices@18,suppliers@unversioned"


def test_a_where_slice_is_applied_and_the_count_guards_the_landing(warehouse, tmp_path):
    warehouse.where_filters["flagged = true"] = lambda row: row["flagged"] == "true"
    config = make_config(tables=[{"name": "invoices", "key": "id", "where": "flagged = true"}])
    outcome = pull(config, tmp_path / "a.duckdb", host="h", token="dapi-test")
    assert outcome.tables[0].rows == 125
    assert all("WHERE (flagged = true)" in s for s in warehouse.statements if "SELECT" in s)
    assert any("WHERE (flagged = true) AND `id` > :last_key" in s for s in warehouse.statements)

    warehouse.count_bias = 1  # the table moved mid-pull
    with pytest.raises(PullError, match="landed 125 rows but the count said 126"):
        pull(config, tmp_path / "b.duckdb", host="h", token="dapi-test")


def test_dry_run_plans_without_credentials():
    lines = plan_statements(make_config())
    text = "\n".join(lines)
    assert "DESCRIBE HISTORY `cat`.`sch`.`invoices` LIMIT 1" in text
    assert "SELECT COUNT(*) AS n FROM `cat`.`sch`.`invoices` VERSION AS OF <version>" in text
    assert "WHERE `id` > :last_key ORDER BY `id` LIMIT 100" in text
    assert "`suppliers` ORDER BY ALL LIMIT 100 OFFSET 100" in text
    assert "DESCRIBE HISTORY `cat`.`sch`.`suppliers`" not in text


def test_base_url_for():
    assert base_url_for("adb-1.azuredatabricks.net/") == "https://adb-1.azuredatabricks.net"
    assert base_url_for("https://x.test") == "https://x.test"


# --- Failure modes, each with the hint the docstring promises ------------


def test_a_bad_token_is_a_401_naming_the_variable(warehouse, tmp_path):
    with pytest.raises(PullError, match=r"HTTP 401 .*DATABRICKS_TOKEN"):
        pull(make_config(), tmp_path / "a.duckdb", host="h", token="wrong")


def test_an_unknown_warehouse_is_a_404(warehouse, tmp_path):
    with pytest.raises(PullError, match=r"HTTP 404 .*warehouse_id"):
        pull(make_config(warehouse_id="nope"), tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_an_unknown_table_carries_the_warehouse_message(warehouse, tmp_path):
    config = make_config(tables=[{"name": "ghosts"}])
    with pytest.raises(PullError, match=r"TABLE_OR_VIEW_NOT_FOUND.*`ghosts`"):
        pull(config, tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_a_view_asked_for_history_points_at_versioned_false(warehouse, tmp_path):
    config = make_config(tables=[{"name": "suppliers"}])  # versioned by default
    with pytest.raises(PullError, match=r"DESCRIBE HISTORY.*versioned: false"):
        pull(config, tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_the_inline_cap_points_at_page_rows(warehouse, tmp_path):
    warehouse.inline_limit_rows = 50
    with pytest.raises(PullError, match=r"inline limit.*lower pull.page_rows"):
        pull(make_config(), tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_an_old_runtime_without_order_by_all_points_at_key(warehouse, tmp_path):
    warehouse.order_by_all_supported = False
    with pytest.raises(PullError, match=r"near 'ALL'.*set `key`"):
        pull(make_config(), tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_an_unmapped_type_asks_for_a_deliberate_mapping(warehouse, tmp_path):
    warehouse.tables["spans"] = FakeTable(
        columns=[("id", "LONG", "BIGINT"), ("span", "INTERVAL", "INTERVAL DAY")],
        rows=[["1", "INTERVAL '1' DAY"]],
    )
    config = make_config(tables=[{"name": "spans", "key": "id"}])
    with pytest.raises(PullError, match=r"spans.span: no DuckDB mapping.*INTERVAL.*TYPE_MAP"):
        pull(config, tmp_path / "a.duckdb", host="h", token="dapi-test")


def test_a_fresh_pull_replaces_the_file(warehouse, tmp_path):
    path = tmp_path / "app.duckdb"
    path.write_bytes(b"not a database")
    pull(make_config(), path, host="h", token="dapi-test")
    assert _query(path, "SELECT COUNT(*) AS n FROM invoices")[0]["n"] == 250


# --- The pulled world feeds the existing machinery ------------------------


def test_the_pulled_world_feeds_the_generators(warehouse, tmp_path):
    """The point of the pull: dictionary and stats run over the file
    exactly as over InvoiceGuard's — and a DECIMAL column, landed as
    DOUBLE, gets its mean."""
    from engine.generators.dictionary import DictionaryGenerator
    from engine.generators.stats import StatsGenerator
    from tests.fixture_generation import IDENTITY

    path = tmp_path / "app.duckdb"
    pull(make_config(), path, host="h", token="dapi-test")
    sql = DuckDbSql(DuckDbSettings(database=str(path)))
    generation = GenerationConfig(component_id_prefix="x", source_globs=[])

    rows, manifest, _warnings = DictionaryGenerator(sql, IDENTITY, generation).generate(
        [], source_commit_sha=None
    )
    tables = sorted({row.table_name for row in rows if row.column_name == ""})
    assert tables == ["invoices", "suppliers"]
    assert manifest.simulation_seed is None

    stats, _ = StatsGenerator(sql, IDENTITY, generation).generate(source_commit_sha=None)
    total = next(r for r in stats if r.table_name == "invoices" and r.column_name == "total")
    assert total.mean is not None and total.mean > 0
