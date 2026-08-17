"""The conformance validator: green on a real generated pack, and each
deliberate corruption FAILs with the offending row named."""

import shutil

import pytest

from engine.adapters.source_code_local import (
    LocalDirectorySource,
    LocalSourceSettings,
)
from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.substrates.jsonl import write_substrate
from engine.substrates.manifest import save_manifest
from engine.validate.conformance import ConformanceValidator
from engine.validate.report import render

from tests.fixture_generation import CONFIG, IDENTITY, SNAPSHOT, generate_all


@pytest.fixture()
def fixture_pack(snapshot_outputs, tmp_path):
    """A complete pack directory generated from the snapshot."""
    pack = tmp_path / "pack"
    substrates = pack / "substrates"
    for substrate, rows in snapshot_outputs.items():
        write_substrate(substrates, substrate, rows)
    # Manifests: regenerate the same content-addressed records.
    from engine.generators import ckg, dictionary, stats
    from engine.substrates.manifest import build_manifest

    tables = sorted({row.table_name for row in snapshot_outputs["dictionary"] if row.column_name == ""})
    sha = "761a18e9b9253870d930f1b13b3a852ce516d603"
    for name, generator_module, source_tables in (
        ("dictionary", dictionary, tables),
        ("stats", stats, tables),
        ("ckg", ckg, []),
    ):
        manifest = build_manifest(
            name,
            generator_module.GENERATOR_VERSION,
            source_commit_sha=sha,
            simulation_seed=CONFIG.simulation_seed,
            source_tables=source_tables,
        )
        save_manifest(substrates / "manifests" / f"{name}.json", manifest)
    shutil.copy(SNAPSHOT / "components.yaml", pack / "components.yaml")
    shutil.copy(SNAPSHOT / "primer.md", pack / "primer.md")
    return pack


def make_validator(snapshot_duckdb):
    return ConformanceValidator(
        DuckDbSql(DuckDbSettings(database=str(snapshot_duckdb))),
        LocalDirectorySource(
            LocalSourceSettings(
                root=str(SNAPSHOT / "source"),
                commit_sha="761a18e9b9253870d930f1b13b3a852ce516d603",
            )
        ),
        IDENTITY,
        "ig",
    )


def test_generated_pack_passes_with_legible_report(fixture_pack, snapshot_duckdb):
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    text = render(report)
    assert report.passed, text
    assert "RESULT: PASS" in text
    assert text.count("[  ok]") >= 6


def corrupt_line(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_dangling_edge_target_fails_naming_the_edge(fixture_pack, snapshot_duckdb):
    nodes = fixture_pack / "substrates" / "ckg_nodes.jsonl"
    lines = nodes.read_text(encoding="utf-8").splitlines(keepends=True)
    removed = [line for line in lines if '"kind":"method"' not in line]
    nodes.write_text("".join(removed), encoding="utf-8")
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "edges resolve" in c.name)
    assert check.status == "FAIL"
    assert any("target node" in detail for detail in check.details)


def test_dangling_manifest_link_fails(fixture_pack, snapshot_duckdb):
    (fixture_pack / "substrates" / "manifests" / "ckg.json").unlink()
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "manifest" in c.name and "link" in c.name)
    assert check.status == "FAIL"
    assert any("unknown manifest" in detail for detail in check.details)


def test_dictionary_column_absent_from_db_fails(fixture_pack, snapshot_duckdb):
    # Corrupt a MACHINE row: orphaned human rows are preserved by
    # design (the generator warns), so only machine drift fails.
    corrupt_line(
        fixture_pack / "substrates" / "dictionary.jsonl",
        '"column_name":"invoice_total"',
        '"column_name":"invoice_total_v2"',
    )
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "live database" in c.name)
    assert check.status == "FAIL"
    assert any("invoice_total_v2" in detail for detail in check.details)


def test_primer_orphan_reference_fails(fixture_pack, snapshot_duckdb):
    primer = fixture_pack / "primer.md"
    primer.write_text(
        primer.read_text(encoding="utf-8") + "\nAlso see ig.spine.no-such.\n",
        encoding="utf-8",
    )
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "primer" in c.name)
    assert check.status == "FAIL"
    assert any("ig.spine.no-such" in detail for detail in check.details)


def test_sha_mismatch_fails(fixture_pack, snapshot_duckdb):
    validator = ConformanceValidator(
        DuckDbSql(DuckDbSettings(database=str(snapshot_duckdb))),
        LocalDirectorySource(
            LocalSourceSettings(
                root=str(SNAPSHOT / "source"), commit_sha="deadbeef" * 5
            )
        ),
        IDENTITY,
        "ig",
    )
    report = validator.validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "manifests share" in c.name)
    assert check.status == "FAIL"
    assert any("line references are invalid" in detail for detail in check.details)


ARTIFACTS = SNAPSHOT.parent / "pack_artifacts"


def add_authored_artifacts(pack):
    shutil.copy(ARTIFACTS / "dictionary_map.yaml", pack / "dictionary_map.yaml")
    shutil.copytree(ARTIFACTS / "business_docs", pack / "business_docs")


def test_dictionary_map_and_business_docs_pass_when_present(
    fixture_pack, snapshot_duckdb
):
    add_authored_artifacts(fixture_pack)
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    map_check = next(c for c in report.checks if "dictionary map" in c.name)
    docs_check = next(c for c in report.checks if "business docs" in c.name)
    assert map_check.status == "PASS", map_check.details
    assert docs_check.status == "PASS", docs_check.details


def test_absent_authored_artifacts_warn_not_fail(fixture_pack, snapshot_duckdb):
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    map_check = next(c for c in report.checks if "dictionary map" in c.name)
    docs_check = next(c for c in report.checks if "business docs" in c.name)
    assert map_check.status == "WARN"
    assert docs_check.status == "WARN"
    assert report.passed


def test_dictionary_map_unknown_column_fails_naming_it(
    fixture_pack, snapshot_duckdb
):
    add_authored_artifacts(fixture_pack)
    corrupt_line(
        fixture_pack / "dictionary_map.yaml",
        "to_column: invoice_id",
        "to_column: invoice_uuid",
    )
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "dictionary map" in c.name)
    assert check.status == "FAIL"
    assert any("findings.invoice_uuid" in detail for detail in check.details)


def test_business_doc_without_front_matter_fails(fixture_pack, snapshot_duckdb):
    add_authored_artifacts(fixture_pack)
    (fixture_pack / "business_docs" / "bad.md").write_text(
        "just markdown, no provenance\n", encoding="utf-8"
    )
    report = make_validator(snapshot_duckdb).validate(fixture_pack, "snapshot")
    check = next(c for c in report.checks if "business docs" in c.name)
    assert check.status == "FAIL"
    assert any("front matter" in detail for detail in check.details)
