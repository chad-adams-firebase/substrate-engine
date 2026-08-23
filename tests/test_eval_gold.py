"""Gold scripts are executed artifacts, and rot is detectable: a
committed expectation that the executed script no longer reproduces
must surface as ROT, never grade silently."""

from pathlib import Path

import pytest

from engine.eval.bank import load_bank
from engine.eval.gold import (
    GoldError,
    check_gold,
    compare_expected,
    run_gold,
)
from engine.eval.world import World, WorldError

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"


@pytest.fixture()
def world(snapshot_duckdb) -> World:
    return World(
        duckdb_path=snapshot_duckdb,
        log_path=SNAPSHOT / "logs" / "invoiceguard.log",
        substrates_dir=SNAPSHOT / "expected",
        components_path=SNAPSHOT / "components.yaml",
    )


def write_gold(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_run_gold_executes_sql_against_the_world(tmp_path, world):
    script = write_gold(
        tmp_path / "count.py",
        "def gold(world):\n"
        "    rows = world.sql('SELECT COUNT(*) AS n FROM invoices')\n"
        "    return {'value': rows[0]['n']}\n",
    )
    executed = run_gold(script, world)
    direct = world.sql("SELECT COUNT(*) AS n FROM invoices")[0]["n"]
    assert direct > 0
    assert executed == {"value": direct}


def test_world_grep_log_matches_raw_lines(world):
    hits = world.grep_log(r"logger=invoiceguard\.")
    assert hits and all("logger=invoiceguard." in line for line in hits)
    assert world.grep_log(r"event=no_such_event_xyz") == []


def test_world_ckg_index_loads_from_substrates(world):
    assert world.ckg.node_by_qualified_name
    name, node = next(iter(world.ckg.node_by_qualified_name.items()))
    assert world.ckg.resolve_node(name) is node


def test_world_missing_database_is_legible(tmp_path):
    broken = World(duckdb_path=tmp_path / "absent.duckdb")
    with pytest.raises(WorldError, match="engine convert"):
        broken.sql("SELECT 1")


def test_run_gold_rejects_bad_scripts(tmp_path, world):
    no_fn = write_gold(tmp_path / "no_fn.py", "value = 3\n")
    with pytest.raises(GoldError, match="no gold"):
        run_gold(no_fn, world)

    raises = write_gold(
        tmp_path / "raises.py",
        "def gold(world):\n    raise RuntimeError('boom')\n",
    )
    with pytest.raises(GoldError, match="raised: boom"):
        run_gold(raises, world)

    not_dict = write_gold(
        tmp_path / "not_dict.py", "def gold(world):\n    return 42\n"
    )
    with pytest.raises(GoldError, match="dict with string keys"):
        run_gold(not_dict, world)


def test_compare_expected_semantics():
    executed = {"value": 146.0, "extra": "roster", "flag": True}
    assert compare_expected({"value": 146}, executed) == []
    assert compare_expected({"flag": True}, executed) == []
    assert compare_expected({"flag": 1}, executed)  # bool is not 1
    assert compare_expected({"missing": 1}, executed) == [
        "missing: missing from executed gold"
    ]
    assert "committed 147" in compare_expected({"value": 147}, executed)[0]


def _bank_with_expected(tmp_path, expected_value: int):
    root = tmp_path / f"bank_{expected_value}"
    (root / "bank").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "eval.yaml").write_text(
        "pack: ../pack\n", encoding="utf-8"
    )
    (root / "gold" / "count.py").write_text(
        "def gold(world):\n"
        "    rows = world.sql('SELECT COUNT(*) AS n FROM invoices')\n"
        "    return {'value': rows[0]['n']}\n",
        encoding="utf-8",
    )
    (root / "bank" / "rows.yaml").write_text(
        "- id: R1\n"
        "  provenance: scripted\n"
        "  category: data\n"
        "  question: how many?\n"
        "  gold: gold/count.py\n"
        f"  expected_gold: {{value: {expected_value}}}\n"
        "  expect:\n"
        "    exit: [0]\n"
        "    assertions: [{kind: numeric_from_gold, field: value}]\n",
        encoding="utf-8",
    )
    return load_bank(root)


def test_check_gold_detects_rot_and_health(tmp_path, world):
    true_count = world.sql("SELECT COUNT(*) AS n FROM invoices")[0]["n"]

    healthy = check_gold(_bank_with_expected(tmp_path, true_count), world)
    assert [c.status for c in healthy] == ["ok"]

    rotten = check_gold(_bank_with_expected(tmp_path, true_count + 1), world)
    assert [c.status for c in rotten] == ["rot"]
    assert "value:" in rotten[0].mismatches[0]
