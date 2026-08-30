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


@pytest.fixture(scope="session")
def snapshot_outputs(snapshot_duckdb) -> dict:
    """All generator outputs for the snapshot, produced exactly the way
    the checked-in expected files were (tests/fixture_generation.py)."""
    from tests.fixture_generation import generate_all

    return generate_all(snapshot_duckdb)


@pytest.fixture
def tool_pack(snapshot_outputs, snapshot_duckdb, tmp_path) -> Path:
    """A complete pack directory for tool tests: generated substrates
    from the snapshot, manifests, authored artifacts, the carved log
    slice, and a config enabling all nine tools. The config names the
    real 'openrouter' LLM adapter; build_tool_registry overrides that
    registry slot with the pytest stub, so the same pack also works
    through the CLI (whose lazy OpenRouter adapter needs no key)."""
    import shutil

    from engine.substrates.jsonl import write_substrate
    from engine.substrates.manifest import build_manifest, save_manifest

    pack = tmp_path / "pack"
    substrates = pack / "substrates"
    for substrate, rows in snapshot_outputs.items():
        write_substrate(substrates, substrate, rows)

    from engine.generators import ckg, dictionary, stats

    sha = "761a18e9b9253870d930f1b13b3a852ce516d603"
    tables = sorted(
        {
            row.table_name
            for row in snapshot_outputs["dictionary"]
            if row.column_name == ""
        }
    )
    for name, module, source_tables in (
        ("dictionary", dictionary, tables),
        ("stats", stats, tables),
        ("ckg", ckg, []),
    ):
        manifest = build_manifest(
            name,
            module.GENERATOR_VERSION,
            source_commit_sha=sha,
            simulation_seed=42,
            source_tables=source_tables,
        )
        save_manifest(substrates / "manifests" / f"{name}.json", manifest)

    artifacts = Path(__file__).parent / "fixtures" / "pack_artifacts"
    shutil.copy(SNAPSHOT / "components.yaml", pack / "components.yaml")
    shutil.copy(SNAPSHOT / "primer.md", pack / "primer.md")
    shutil.copy(artifacts / "dictionary_map.yaml", pack / "dictionary_map.yaml")
    shutil.copytree(artifacts / "business_docs", pack / "business_docs")

    config = {
        "name": "toolpack",
        "substrates": [
            "data_dictionary",
            "data_dictionary_map",
            "univariate_statistics",
            "code_knowledge_graph",
            "ckg_components",
            "primer",
            "source_code",
            "application_database",
            "application_logs",
            "business_context_docs",
        ],
        "tools": [
            "query_univariate_stats",
            "lookup_data_dictionary",
            "traverse_code_knowledge_graph",
            "run_sql",
            "read_source",
            "app_primer",
            "search_business_docs",
            "check_execution",
            "answer_from_known_items",
        ],
        "adapters": {
            "llm": {"adapter": "openrouter", "settings": {"model": "openrouter/auto"}},
            "sql": {"adapter": "duckdb", "settings": {"database": str(snapshot_duckdb)}},
            "work_store": {"adapter": "sqlite", "settings": {"database": ":memory:"}},
            "identity": {
                "adapter": "fake_user",
                "settings": {"username": "tester", "display_name": "Test User"},
            },
            "source_code": {
                "adapter": "local_directory",
                "settings": {
                    "root": str(SNAPSHOT / "source"),
                    "commit_sha": "761a18e9b9253870d930f1b13b3a852ce516d603",
                },
            },
            "substrate_store": {"adapter": "pack_files", "settings": {}},
            "execution_log": {
                "adapter": "logfmt_file",
                "settings": {
                    "path": str(SNAPSHOT / "logs" / "invoiceguard.log"),
                    "components": {
                        "stale_sweep": {
                            "logger": "invoiceguard.lapse_lifecycle",
                            "ran_event": "stale_sweep_completed",
                        },
                        "benchmark_scoring": {
                            "logger": "invoiceguard.benchmark_scoring",
                            "ran_event": "benchmark_scored",
                            "key_field": "invoice_id",
                            "error_levels": ["WARNING", "ERROR"],
                        },
                    },
                },
            },
        },
        "display": {"money": {"symbol": "$"}},
    }
    (pack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return pack


def build_tool_registry(pack_dir: Path, llm_responses: list | None = None):
    """load → DI-build → tool-build, with the pytest LLM stub wired in
    over the 'openrouter' registry slot. Returns (registry, ports)."""
    from engine.config.models import PortName
    from engine.config.pack_loader import load_pack
    from engine.runtime.container import build
    from engine.runtime.registry import default_registry
    from engine.runtime.tools import build_tools
    from tests.stubs.llm_stub import ScriptedLLM

    pack = load_pack(pack_dir)
    registry = default_registry()
    stub = ScriptedLLM(llm_responses or [])
    registry.register(PortName.LLM, "openrouter", lambda settings, root: stub)
    ports = build(pack, registry)
    return build_tools(pack, ports), ports


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
