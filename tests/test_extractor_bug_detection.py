"""The drift-catch acceptance test (phasing Phase 2 "done"): a
deliberately introduced extractor bug — simulated by mutating a copy
of the fixture source — must fail the expected-output comparison, and
fail it legibly (the missing edge is nameable)."""

import shutil

from engine.adapters.source_code_local import (
    LocalDirectorySource,
    LocalSourceSettings,
)
from engine.generators.ckg import CkgGenerator, node_id
from engine.substrates.jsonl import write_substrate
from engine.substrates.pack_data import load_components

from tests.fixture_generation import CONFIG, EXPECTED, SNAPSHOT


def mutated_snapshot(tmp_path, transform):
    root = tmp_path / "mutated"
    shutil.copytree(SNAPSHOT / "source", root)
    target = root / "src/invoiceguard/spine/lapse_lifecycle.py"
    target.write_text(
        transform(target.read_text(encoding="utf-8")), encoding="utf-8"
    )
    return root


def extract(root):
    source = LocalDirectorySource(
        LocalSourceSettings(root=str(root), commit_sha="761a18e9")
    )
    return CkgGenerator(source, CONFIG).generate(
        load_components(SNAPSHOT / "components.yaml"), [], None
    )


def test_changed_raw_sql_table_is_caught(tmp_path):
    """FROM invoices -> FROM invoices_v2 in STALE_CANDIDATES_SQL: the
    reads edge moves, so the byte comparison fails AND the specific
    regression is nameable."""
    root = mutated_snapshot(
        tmp_path, lambda text: text.replace("FROM invoices", "FROM invoices_v2")
    )
    result = extract(root)
    written = write_substrate(tmp_path, "ckg_edges", result.edges)
    assert written.read_bytes() != EXPECTED.joinpath("ckg_edges.jsonl").read_bytes()

    sweep = node_id("invoiceguard.spine.lapse_lifecycle.run_stale_sweep", "function")
    raw_sql_reads = {
        edge.target_table
        for edge in result.edges
        if edge.source_id == sweep and edge.kind == "reads_table"
    }
    assert "invoices_v2" in raw_sql_reads


def test_deleted_write_site_is_caught(tmp_path):
    """Removing the session.add(entry) write inside a copy of the
    transition helper drops the writes edge; comparison fails."""
    root = tmp_path / "mutated"
    shutil.copytree(SNAPSHOT / "source", root)
    helper = root / "src/invoiceguard/models/invoice.py"
    text = helper.read_text(encoding="utf-8")
    assert "session.add(entry)" in text
    helper.write_text(
        text.replace("session.add(entry)", "pass  # write removed"),
        encoding="utf-8",
    )
    result = extract(root)
    written = write_substrate(tmp_path, "ckg_edges", result.edges)
    assert written.read_bytes() != EXPECTED.joinpath("ckg_edges.jsonl").read_bytes()

    transition = node_id(
        "invoiceguard.models.invoice.Invoice.transition_to", "method"
    )
    assert not any(
        edge.source_id == transition and edge.kind == "writes_table"
        for edge in result.edges
    )
