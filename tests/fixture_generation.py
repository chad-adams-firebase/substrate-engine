"""One shared definition of "generate everything against the snapshot".

Used two ways:
- by generator tests, to produce fresh output for byte-comparison
  against the checked-in expected files;
- as a module command to deliberately regenerate those expected files
  after an intentional extractor change (CLAUDE.md testing law: a
  changed extractor changes fixtures deliberately):

      uv run python -m tests.fixture_generation --write

Both paths share this code so the tests can never drift from the way
the expectations were produced.
"""

import argparse
import sqlite3
import tempfile
from pathlib import Path

from pydantic import BaseModel

from engine.adapters.source_code_local import LocalDirectorySource, LocalSourceSettings
from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.config.models import GenerationConfig
from engine.generators.ckg import CkgGenerator
from engine.generators.dictionary import DictionaryGenerator
from engine.generators.stats import StatsGenerator
from engine.packtools.convert_sqlite import convert
from engine.ports.types import User
from engine.substrates.jsonl import write_substrate
from engine.substrates.pack_data import (
    load_components,
    load_dictionary_overlay,
    load_primer,
)

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"
EXPECTED = SNAPSHOT / "expected"
COMMIT_SHA = "761a18e9b9253870d930f1b13b3a852ce516d603"

# Pinned generation settings for the snapshot. Changing these changes
# the expected files — deliberately.
CONFIG = GenerationConfig(
    source_sqlite="db",
    simulation_seed=42,
    component_id_prefix="ig",
    source_globs=["src/invoiceguard/**/*.py"],
    exclude_globs=[],
    enum_scan_max_distinct=12,
)
IDENTITY = User(username="generator", display_name="Generator")


def build_snapshot_duckdb(destination: Path, snapshot: Path = SNAPSHOT) -> Path:
    sqlite_path = destination / "invoiceguard.db"
    connection = sqlite3.connect(str(sqlite_path))
    try:
        connection.executescript(
            (snapshot / "db" / "schema.sql").read_text(encoding="utf-8")
            + (snapshot / "db" / "data.sql").read_text(encoding="utf-8")
        )
        connection.commit()
    finally:
        connection.close()
    duckdb_path = destination / "app.duckdb"
    convert(
        sqlite_path,
        duckdb_path,
        source_commit_sha=COMMIT_SHA,
        simulation_seed=CONFIG.simulation_seed,
    )
    return duckdb_path


def generate_all(
    duckdb_path: Path, snapshot: Path = SNAPSHOT
) -> dict[str, list[BaseModel]]:
    """All machine substrates for the snapshot, keyed by substrate
    file name. Deterministic by construction."""
    sql = DuckDbSql(DuckDbSettings(database=str(duckdb_path)))
    source = LocalDirectorySource(
        LocalSourceSettings(root=str(snapshot / "source"), commit_sha=COMMIT_SHA)
    )
    components = load_components(snapshot / "components.yaml")
    overlay = load_dictionary_overlay(snapshot / "overlays" / "dictionary.jsonl")
    primer = load_primer(snapshot / "primer.md")

    dictionary_rows, _, _ = DictionaryGenerator(sql, IDENTITY, CONFIG).generate(
        overlay, source_commit_sha=COMMIT_SHA
    )
    stats_rows, _ = StatsGenerator(sql, IDENTITY, CONFIG).generate(
        source_commit_sha=COMMIT_SHA
    )
    extraction = CkgGenerator(source, CONFIG).generate(components, [], primer)
    assert not extraction.errors, extraction.errors

    return {
        "dictionary": dictionary_rows,
        "univariate_stats": stats_rows,
        "ckg_nodes": extraction.nodes,
        "ckg_edges": extraction.edges,
        "ckg_conditionals": extraction.conditionals,
        "component_memberships": extraction.memberships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        required=True,
        help="Rewrite tests/fixtures/invoiceguard_snapshot/expected/.",
    )
    parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        duckdb_path = build_snapshot_duckdb(Path(scratch))
        for substrate, rows in generate_all(duckdb_path).items():
            path = write_substrate(EXPECTED, substrate, rows)
            print(f"wrote {path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
