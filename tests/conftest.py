import sqlite3
from pathlib import Path

import pytest
import yaml

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"


@pytest.fixture(scope="session")
def snapshot_sqlite(tmp_path_factory) -> Path:
    """The vendored DB slice, materialized as a real SQLite file.

    Built from checked-in SQL text (the repo gitignores binary
    databases) so the slice stays diffable and the carve stays
    reviewable."""
    path = tmp_path_factory.mktemp("snapshot") / "invoiceguard.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            (SNAPSHOT / "db" / "schema.sql").read_text(encoding="utf-8")
            + (SNAPSHOT / "db" / "data.sql").read_text(encoding="utf-8")
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture(scope="session")
def snapshot_duckdb(snapshot_sqlite, tmp_path_factory) -> Path:
    """The slice converted to DuckDB — every dictionary/stats test
    that uses this also exercises the converter."""
    from engine.packtools.convert_sqlite import convert

    path = tmp_path_factory.mktemp("converted") / "app.duckdb"
    convert(
        snapshot_sqlite,
        path,
        source_commit_sha="761a18e9b9253870d930f1b13b3a852ce516d603",
        simulation_seed=42,
    )
    return path

# A complete, valid pack config used as the baseline by loader and
# container tests; individual tests override pieces to probe failures.
VALID_CONFIG: dict = {
    "name": "testpack",
    "description": "Pack used by unit tests.",
    "substrates": ["data_dictionary", "application_database"],
    "tools": ["lookup_data_dictionary", "run_sql"],
    "adapters": {
        "llm": {
            "adapter": "openrouter",
            "settings": {"model": "openrouter/auto"},
        },
        "sql": {
            "adapter": "duckdb",
            "settings": {"database": ":memory:"},
        },
        "work_store": {
            "adapter": "sqlite",
            "settings": {"database": ":memory:"},
        },
        "identity": {
            "adapter": "fake_user",
            "settings": {"username": "tester", "display_name": "Test User"},
        },
        "source_code": {
            "adapter": "local_directory",
            "settings": {"root": ".", "commit_sha": "abc1234"},
        },
    },
}


@pytest.fixture
def make_pack(tmp_path: Path):
    """Write a pack directory from a config dict and return its path."""

    def _make(config: dict, name: str = "pack") -> Path:
        pack_dir = tmp_path / name
        pack_dir.mkdir()
        (pack_dir / "config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        return pack_dir

    return _make
