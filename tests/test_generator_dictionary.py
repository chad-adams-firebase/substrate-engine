"""Dictionary generator against the vendored snapshot + expected output."""

from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import EXPECTED


def expected_bytes(substrate: str) -> bytes:
    return (EXPECTED / f"{substrate}.jsonl").read_bytes()


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(tmp_path, "dictionary", snapshot_outputs["dictionary"])
    assert path.read_bytes() == expected_bytes("dictionary")


def test_structural_facts(snapshot_outputs):
    rows = {
        (row.table_name, row.column_name): row
        for row in snapshot_outputs["dictionary"]
    }
    assert ("invoices", "") in rows  # table-level row
    invoice_id = rows[("invoices", "id")]
    assert invoice_id.is_primary_key and invoice_id.data_type == "BIGINT"
    assert rows[("invoices", "supplier_id")].fk_target == "suppliers.id"
    assert rows[("invoices", "prior_revision_id")].fk_target == "invoices.id"
    assert rows[("invoices", "po_reference")].nullable is True


def test_data_scan_enums_are_labeled_and_low_confidence(snapshot_outputs):
    rows = {
        (row.table_name, row.column_name): row
        for row in snapshot_outputs["dictionary"]
    }
    status = rows[("invoices", "status")]
    assert status.enum_source == "data_scan"
    # The slice's invoices are all terminal — the scan reports what the
    # data shows and says so via low confidence + needs_validation.
    assert status.enum_values == ["CLOSED", "LAPSED", "NO_REVIEW_NEEDED"]
    assert status.provenance.confidence == 0.5
    assert status.provenance.needs_validation is True
    # Free-text columns must never be mistaken for enums.
    assert rows[("findings", "description")].enum_values is None


def test_orphaned_overlay_row_is_preserved_and_warned(snapshot_duckdb):
    from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
    from engine.generators.dictionary import DictionaryGenerator
    from engine.substrates.models import DictionaryRow, Provenance

    from tests.fixture_generation import CONFIG, IDENTITY

    orphan = DictionaryRow(
        table_name="invoices",
        column_name="legacy_column",
        description="A column that no longer exists.",
        provenance=Provenance(
            source="human",
            confidence=1.0,
            last_confirmed_by="sme",
            needs_validation=False,
        ),
    )
    sql = DuckDbSql(DuckDbSettings(database=str(snapshot_duckdb)))
    rows, _, warnings = DictionaryGenerator(sql, IDENTITY, CONFIG).generate(
        [orphan], source_commit_sha="761a18e9"
    )
    assert any("legacy_column" in warning for warning in warnings)
    preserved = next(
        row
        for row in rows
        if (row.table_name, row.column_name) == ("invoices", "legacy_column")
    )
    assert preserved.description == "A column that no longer exists."
    assert preserved.provenance.source == "human"
    assert preserved.provenance.needs_validation is True
