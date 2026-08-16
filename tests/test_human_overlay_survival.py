"""The sacred-rows acceptance test (CLAUDE.md data law): regeneration
overwrites only source=machine rows; human rows survive untouched."""

from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.generators.dictionary import DictionaryGenerator
from engine.substrates.jsonl import dumps_row
from engine.substrates.pack_data import load_dictionary_overlay

from tests.fixture_generation import CONFIG, IDENTITY, SNAPSHOT


def generate(snapshot_duckdb):
    sql = DuckDbSql(DuckDbSettings(database=str(snapshot_duckdb)))
    overlay = load_dictionary_overlay(SNAPSHOT / "overlays" / "dictionary.jsonl")
    rows, manifest, warnings = DictionaryGenerator(
        sql, IDENTITY, CONFIG
    ).generate(overlay, source_commit_sha="761a18e9")
    return rows, manifest, warnings


def human_rows(rows):
    return {
        (row.table_name, row.column_name): row
        for row in rows
        if row.provenance.source == "human"
    }


def test_human_row_survives_regeneration_byte_identical(snapshot_duckdb):
    first_rows, first_manifest, _ = generate(snapshot_duckdb)
    second_rows, second_manifest, _ = generate(snapshot_duckdb)

    first_humans = human_rows(first_rows)
    second_humans = human_rows(second_rows)
    assert ("invoices", "adjustment_flag") in first_humans
    assert {
        key: dumps_row(row) for key, row in first_humans.items()
    } == {key: dumps_row(row) for key, row in second_humans.items()}

    survivor = second_humans[("invoices", "adjustment_flag")]
    assert survivor.provenance.last_confirmed_by == "sme.fixture"
    assert survivor.provenance.needs_validation is False
    assert survivor.provenance.manifest_id is None
    # The human's words are verbatim; the structure is machine-fresh.
    assert survivor.description.startswith("When true")
    assert survivor.data_type == "BOOLEAN"

    # Machine rows meanwhile carry the (identical) fresh manifest.
    assert first_manifest.manifest_id == second_manifest.manifest_id
    machine_row = next(
        row for row in second_rows if row.provenance.source == "machine"
    )
    assert machine_row.provenance.manifest_id == second_manifest.manifest_id
