"""CKG node extraction: kinds, locations, signatures, constants, and
content-addressed identity."""

from engine.generators.ckg import CkgGenerator, node_id
from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import CONFIG, EXPECTED, SNAPSHOT


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(tmp_path, "ckg_nodes", snapshot_outputs["ckg_nodes"])
    assert path.read_bytes() == EXPECTED.joinpath("ckg_nodes.jsonl").read_bytes()


def by_name(snapshot_outputs):
    return {node.qualified_name: node for node in snapshot_outputs["ckg_nodes"]}


def test_every_kind_is_extracted(snapshot_outputs):
    nodes = by_name(snapshot_outputs)
    assert nodes["invoiceguard.spine.lapse_lifecycle"].kind == "module"
    assert nodes["invoiceguard.models.invoice.Invoice"].kind == "class"
    assert nodes["invoiceguard.spine.lapse_lifecycle.run_stale_sweep"].kind == "function"
    assert nodes["invoiceguard.models.invoice.Invoice.transition_to"].kind == "method"
    assert nodes["invoiceguard.spine.lapse_lifecycle.LAPSE_GRACE_DAYS"].kind == "constant"


def test_locations_signatures_docstrings(snapshot_outputs):
    nodes = by_name(snapshot_outputs)
    sweep = nodes["invoiceguard.spine.lapse_lifecycle.run_stale_sweep"]
    assert sweep.file_path == "src/invoiceguard/spine/lapse_lifecycle.py"
    assert sweep.start_line == 62  # def line, verified against the pin
    assert sweep.signature.startswith("(session: Session")
    assert "stale_sweep" in sweep.docstring


def test_sql_constants_capture_their_sql(snapshot_outputs):
    nodes = by_name(snapshot_outputs)
    stale = nodes["invoiceguard.spine.lapse_lifecycle.STALE_CANDIDATES_SQL"]
    assert stale.kind == "constant"
    assert "SELECT id FROM invoices" in stale.value
    rollup = nodes["invoiceguard.platform.api.teams.PRODUCTION_ROLLUP_SQL"]
    assert "FROM invoice_history" in rollup.value
    grace = nodes["invoiceguard.spine.lapse_lifecycle.LAPSE_GRACE_DAYS"]
    assert grace.value == "1"


def test_ids_are_content_addressed_and_walk_order_independent(
    snapshot_outputs, monkeypatch
):
    """Reversing list_files() must not move a single id or byte —
    identity comes from (qualified name, kind), never position."""
    from engine.adapters.source_code_local import (
        LocalDirectorySource,
        LocalSourceSettings,
    )
    from engine.substrates.pack_data import load_components

    source = LocalDirectorySource(
        LocalSourceSettings(root=str(SNAPSHOT / "source"), commit_sha="761a18e9")
    )
    original = source.list_files()
    monkeypatch.setattr(
        source, "list_files", lambda: list(reversed(original))
    )
    reversed_run = CkgGenerator(source, CONFIG).generate(
        load_components(SNAPSHOT / "components.yaml"), [], None
    )
    assert {node.id for node in reversed_run.nodes} == {
        node.id for node in snapshot_outputs["ckg_nodes"]
    }
    sweep = node_id("invoiceguard.spine.lapse_lifecycle.run_stale_sweep", "function")
    assert any(node.id == sweep for node in reversed_run.nodes)
