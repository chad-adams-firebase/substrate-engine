"""Stats generator against the snapshot + hand-computed slice values."""

from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import EXPECTED


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(
        tmp_path, "univariate_stats", snapshot_outputs["univariate_stats"]
    )
    assert path.read_bytes() == EXPECTED.joinpath("univariate_stats.jsonl").read_bytes()


def stats_for(snapshot_outputs, table, column):
    return next(
        row
        for row in snapshot_outputs["univariate_stats"]
        if (row.table_name, row.column_name) == (table, column)
    )


def test_counts_against_the_known_slice(snapshot_outputs):
    """The carve pinned these counts (fixture_manifest.json): 50
    invoices, 40 suppliers."""
    invoice_id = stats_for(snapshot_outputs, "invoices", "id")
    assert invoice_id.row_count == 50
    assert invoice_id.distinct_count == 50
    assert invoice_id.null_rate == 0.0
    assert (invoice_id.min_value, invoice_id.max_value) == ("1", "50")

    supplier_id = stats_for(snapshot_outputs, "suppliers", "id")
    assert supplier_id.row_count == 40


def test_empty_table_is_reported_not_skipped(snapshot_outputs):
    """The slice ships report tables schema-only; stats must say so."""
    row = stats_for(snapshot_outputs, "review_reports", "id")
    assert row.row_count == 0
    assert row.null_rate == 0.0
    assert row.min_value is None and row.max_value is None
    assert row.top_values == []


def test_top_values_are_count_desc_then_value_asc(snapshot_outputs):
    status = stats_for(snapshot_outputs, "invoices", "status")
    counts = [top.count for top in status.top_values]
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) == 50  # 3 statuses cover all 50 slice invoices


def test_mean_only_for_numeric_columns(snapshot_outputs):
    assert stats_for(snapshot_outputs, "invoices", "invoice_total").mean is not None
    assert stats_for(snapshot_outputs, "invoices", "status").mean is None
    assert stats_for(snapshot_outputs, "invoices", "adjustment_flag").mean is None
