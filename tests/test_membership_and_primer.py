"""L1 membership proposals (slug mapping, overlay, warnings) and the
L0 primer reference check."""

from engine.generators.ckg import CkgGenerator, node_id
from engine.generators.ckg.membership import propose_component_id
from engine.generators.ckg.primer_check import check_primer
from engine.substrates.jsonl import write_substrate
from engine.substrates.models import ComponentMembership, Provenance
from engine.substrates.pack_data import load_components

from tests.fixture_generation import CONFIG, EXPECTED, SNAPSHOT

COMPONENT_IDS = {
    "ig.spine.rules-engine",
    "ig.spine.queue",
    "ig.spine.lapse-lifecycle",
    "ig.platform.api",
}


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(
        tmp_path, "component_memberships", snapshot_outputs["component_memberships"]
    )
    assert path.read_bytes() == EXPECTED.joinpath(
        "component_memberships.jsonl"
    ).read_bytes()


def test_slug_mapping_hyphenates_underscores():
    assert (
        propose_component_id(
            "invoiceguard.spine.lapse_lifecycle", "ig", COMPONENT_IDS
        )
        == "ig.spine.lapse-lifecycle"
    )
    # Package-level fallback: api/teams.py belongs to the api component.
    assert (
        propose_component_id(
            "invoiceguard.platform.api.teams", "ig", COMPONENT_IDS
        )
        == "ig.platform.api"
    )
    assert (
        propose_component_id("invoiceguard.models.invoice", "ig", COMPONENT_IDS)
        is None
    )


def test_proposals_are_flagged_machine_and_unvalidated(snapshot_outputs):
    memberships = snapshot_outputs["component_memberships"]
    assert {row.component_id for row in memberships} == COMPONENT_IDS
    for row in memberships:
        assert row.provenance.source == "machine"
        assert row.provenance.needs_validation is True
        assert row.provenance.confidence == 0.6


def test_models_modules_land_unassigned_with_warnings(snapshot_duckdb):
    from engine.adapters.source_code_local import (
        LocalDirectorySource,
        LocalSourceSettings,
    )

    source = LocalDirectorySource(
        LocalSourceSettings(root=str(SNAPSHOT / "source"), commit_sha="761a18e9")
    )
    result = CkgGenerator(source, CONFIG).generate(
        load_components(SNAPSHOT / "components.yaml"), [], None
    )
    unassigned = [w for w in result.warnings if "matches no component" in w]
    assert any("invoiceguard.models.invoice" in w for w in unassigned)


def test_human_membership_row_suppresses_the_machine_proposal():
    from engine.adapters.source_code_local import (
        LocalDirectorySource,
        LocalSourceSettings,
    )

    human = ComponentMembership(
        component_id="ig.platform.api",
        ckg_node_id="",
        node_qualified_name="invoiceguard.spine.queue",
        provenance=Provenance(
            source="human",
            confidence=1.0,
            last_confirmed_by="sme",
            needs_validation=False,
        ),
    )
    source = LocalDirectorySource(
        LocalSourceSettings(root=str(SNAPSHOT / "source"), commit_sha="761a18e9")
    )
    result = CkgGenerator(source, CONFIG).generate(
        load_components(SNAPSHOT / "components.yaml"), [human], None
    )
    queue_rows = [
        row
        for row in result.memberships
        if row.node_qualified_name == "invoiceguard.spine.queue"
    ]
    assert len(queue_rows) == 1
    assert queue_rows[0].provenance.source == "human"
    assert queue_rows[0].component_id == "ig.platform.api"
    # The merge resolves the node id the human could not compute.
    assert queue_rows[0].ckg_node_id == node_id("invoiceguard.spine.queue", "module")


def test_primer_bogus_reference_is_an_error():
    errors, _ = check_primer(
        "Scoring happens in ig.spine.rules-engine and ig.spine.no-such.",
        "ig",
        COMPONENT_IDS,
    )
    assert errors == ["primer references unknown component ig.spine.no-such"]


def test_primer_unreferenced_component_is_a_warning(snapshot_outputs):
    """The fixture primer references 3 of 4 components; the orphaned
    api component surfaces as a warning, not an error."""
    primer = (SNAPSHOT / "primer.md").read_text(encoding="utf-8")
    errors, warnings = check_primer(primer, "ig", COMPONENT_IDS)
    assert errors == []
    assert warnings == [
        "component ig.platform.api is never referenced in the primer"
    ]
