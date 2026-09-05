"""manifest_id is content-addressed over the pinning tuple only."""

from datetime import UTC, datetime

from engine.substrates.manifest import build_manifest, load_manifest, save_manifest


def test_same_pinning_same_id_despite_timestamps():
    a = build_manifest(
        "ckg",
        "1.0.0",
        source_commit_sha="761a18e9",
        simulation_seed=42,
        extracted_at=datetime(2026, 8, 16, 10, 0, tzinfo=UTC),
    )
    b = build_manifest(
        "ckg",
        "1.0.0",
        source_commit_sha="761a18e9",
        simulation_seed=42,
        extracted_at=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
    )
    assert a.manifest_id == b.manifest_id
    assert a.extracted_at != b.extracted_at


def test_distinct_pinning_distinct_id():
    base = dict(source_commit_sha="761a18e9", simulation_seed=42)
    original = build_manifest("stats", "1.0.0", **base)
    assert build_manifest("stats", "1.0.1", **base).manifest_id != original.manifest_id
    assert (
        build_manifest("stats", "1.0.0", source_commit_sha="deadbeef", simulation_seed=42).manifest_id
        != original.manifest_id
    )
    assert (
        build_manifest("stats", "1.0.0", source_commit_sha="761a18e9", simulation_seed=7).manifest_id
        != original.manifest_id
    )


def test_source_tables_are_order_insensitive():
    a = build_manifest("dictionary", "1.0.0", source_tables=["users", "invoices"])
    b = build_manifest("dictionary", "1.0.0", source_tables=["invoices", "users"])
    assert a.manifest_id == b.manifest_id
    assert a.source_tables == ["invoices", "users"]


def test_save_load_round_trip(tmp_path):
    manifest = build_manifest(
        "sqlite_convert", "1.0.0", source_commit_sha="761a18e9", simulation_seed=42
    )
    path = tmp_path / "manifests" / "sqlite_convert.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_source_snapshot_pins_a_pulled_world_without_moving_older_ids():
    """A pulled world's pin is the warehouse schema plus each table's
    Delta version. It joins the hash only when present, so every
    manifest written before the field existed keeps its id (the
    fixtures and reports pin those ids)."""
    without = build_manifest("dictionary", "1.0.0", source_tables=["invoices"])
    explicit_none = build_manifest(
        "dictionary", "1.0.0", source_tables=["invoices"], source_snapshot=None
    )
    assert without.manifest_id == explicit_none.manifest_id
    assert without.source_snapshot is None

    pulled = build_manifest(
        "databricks_pull",
        "1.0.0",
        source_tables=["invoices"],
        source_snapshot="cat.sch|invoices@17",
    )
    again = build_manifest(
        "databricks_pull",
        "1.0.0",
        source_tables=["invoices"],
        source_snapshot="cat.sch|invoices@17",
    )
    moved = build_manifest(
        "databricks_pull",
        "1.0.0",
        source_tables=["invoices"],
        source_snapshot="cat.sch|invoices@18",
    )
    assert pulled.manifest_id == again.manifest_id
    assert pulled.manifest_id != moved.manifest_id
    assert pulled.source_snapshot == "cat.sch|invoices@17"


def test_a_none_field_is_absent_from_the_file(tmp_path):
    """Regeneration's honest one-line diff (the timestamp) must not grow
    a "source_snapshot": null line on every manifest that predates the
    field — found by following the runbook in a fresh clone."""
    import json

    manifest = build_manifest("stats", "1.0.0", source_commit_sha="761a18e9", simulation_seed=42)
    path = tmp_path / "stats.json"
    save_manifest(path, manifest)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "source_snapshot" not in written
    assert load_manifest(path) == manifest
