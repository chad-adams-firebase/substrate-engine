"""CKG edge extraction — the hardest-part fixtures (Brief §5 L2).

The reads/writes assertions here are the contract for what the
extractor detects: both sanctioned raw-SQL sites, the bounded ORM
patterns, and the call/import/contains structure.
"""

from engine.generators.ckg import node_id
from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import EXPECTED


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(tmp_path, "ckg_edges", snapshot_outputs["ckg_edges"])
    assert path.read_bytes() == EXPECTED.joinpath("ckg_edges.jsonl").read_bytes()


def edges_from(snapshot_outputs, qualified_name, kind_of_node, edge_kind):
    source = node_id(qualified_name, kind_of_node)
    return [
        edge
        for edge in snapshot_outputs["ckg_edges"]
        if edge.source_id == source and edge.kind == edge_kind
    ]


def test_raw_sql_site_1_stale_sweep(snapshot_outputs):
    """spine/lapse_lifecycle.py: session.execute(STALE_CANDIDATES_SQL)
    — a module-level text() constant — must yield reads invoices."""
    reads = edges_from(
        snapshot_outputs,
        "invoiceguard.spine.lapse_lifecycle.run_stale_sweep",
        "function",
        "reads_table",
    )
    assert "invoices" in {edge.target_table for edge in reads}


def test_raw_sql_site_2_production_rollup(snapshot_outputs):
    """platform/api/teams.py: the 30-day rollup joins three tables."""
    reads = edges_from(
        snapshot_outputs,
        "invoiceguard.platform.api.teams.production_rollup",
        "function",
        "reads_table",
    )
    assert {edge.target_table for edge in reads} == {
        "invoice_history",
        "users",
        "invoices",
    }


def test_orm_read_patterns(snapshot_outputs):
    # select(Model) inside build_eligible_query
    reads = edges_from(
        snapshot_outputs,
        "invoiceguard.spine.queue.build_eligible_query",
        "function",
        "reads_table",
    )
    assert "invoices" in {edge.target_table for edge in reads}
    # session.get(Invoice, ...) in the stale sweep
    reads = edges_from(
        snapshot_outputs,
        "invoiceguard.spine.lapse_lifecycle.run_stale_sweep",
        "function",
        "reads_table",
    )
    assert {edge.target_table for edge in reads} == {"invoices"}


def test_orm_write_patterns(snapshot_outputs):
    """session.add(entry) where entry = InvoiceHistory(...) — the
    tracked-local pattern, via the single transition helper."""
    writes = edges_from(
        snapshot_outputs,
        "invoiceguard.models.invoice.Invoice.transition_to",
        "method",
        "writes_table",
    )
    assert {edge.target_table for edge in writes} == {"invoice_history"}
    writes = edges_from(
        snapshot_outputs,
        "invoiceguard.models.finding.persist_finding",
        "function",
        "writes_table",
    )
    assert {edge.target_table for edge in writes} == {"findings"}


def test_call_edges_resolve_across_modules(snapshot_outputs):
    """run_stale_sweep -> Invoice.transition_to resolves through the
    models package re-export (simple-name fallback)."""
    calls = edges_from(
        snapshot_outputs,
        "invoiceguard.spine.lapse_lifecycle.run_stale_sweep",
        "function",
        "calls",
    )
    transition = node_id(
        "invoiceguard.models.invoice.Invoice.transition_to", "method"
    )
    assert transition in {edge.target_node_id for edge in calls}


def test_contains_and_imports(snapshot_outputs):
    contains = edges_from(
        snapshot_outputs,
        "invoiceguard.models.invoice.Invoice",
        "class",
        "contains",
    )
    transition = node_id(
        "invoiceguard.models.invoice.Invoice.transition_to", "method"
    )
    assert transition in {edge.target_node_id for edge in contains}

    imports = edges_from(
        snapshot_outputs,
        "invoiceguard.spine.lapse_lifecycle",
        "module",
        "imports",
    )
    models_package = node_id("invoiceguard.models", "module")
    assert models_package in {edge.target_node_id for edge in imports}
