"""Conditionals carry the named thresholds that make "do we always
flag X over $Y?" answerable (Brief §5)."""

from engine.generators.ckg import node_id
from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import EXPECTED


def test_output_matches_checked_in_expectation(snapshot_outputs, tmp_path):
    path = write_substrate(
        tmp_path, "ckg_conditionals", snapshot_outputs["ckg_conditionals"]
    )
    assert path.read_bytes() == EXPECTED.joinpath("ckg_conditionals.jsonl").read_bytes()


def test_named_threshold_appears_in_condition_text(snapshot_outputs):
    """The rate-variance rule's threshold is the config-tunable
    rate_variance_pct; its conditional must carry that name."""
    owner = node_id("invoiceguard.spine.rules_engine.rule_rate_variance", "function")
    conditions = [
        row.condition_text
        for row in snapshot_outputs["ckg_conditionals"]
        if row.node_id == owner
    ]
    assert any("rate_variance_pct" in text for text in conditions)


def test_terminal_guard_condition_is_captured(snapshot_outputs):
    """transition_to's terminal-status guard, including the sanctioned
    LAPSED reactivation escape hatch."""
    owner = node_id(
        "invoiceguard.models.invoice.Invoice.transition_to", "method"
    )
    conditions = [
        row.condition_text
        for row in snapshot_outputs["ckg_conditionals"]
        if row.node_id == owner
    ]
    assert any("TERMINAL_STATUSES" in text for text in conditions)
