"""The REAL InvoiceGuard Dictionary Map, as the pack ships it: every
hand-authored entry loads, refers to columns the dictionary knows, and
does what the coverage pass added it for (Phase 5 interlude, Play
Session #2 — the review/correction subsystem had no map coverage and
produced three wrong-but-verified answers).

Offline: the map, the generated dictionary, and the stats substrate
are committed files. No database, no clone, no LLM."""

from pathlib import Path

import pytest

from engine.substrates.jsonl import read_rows
from engine.substrates.models import DictionaryRow, StatsRow
from engine.substrates.pack_data import load_dictionary_map
from engine.tools.grounding import match_metrics, render_grounding
from engine.tools.sql_lint import lint_fan_out
from engine.validate.conformance import ConformanceValidator

PACK = Path(__file__).parent.parent / "packs" / "invoiceguard"

# The play session's questions, verbatim, and the metric each must
# retrieve as its statement template (retrieval beats exhortation).
PLAY_SESSION_2 = {
    "When auditors request corrections, what rate do they ask for?": (
        "requested_rate_is_contract_rate"
    ),
    "How often do suppliers actually apply the corrections we request?": (
        "correction_application_rate"
    ),
    "Which supplier ignores our corrections the most? How many?": (
        "corrections_ignored_by_supplier"
    ),
    "List audit rejections by reviewer": "corrections_ignored_by_auditor",
}


@pytest.fixture(scope="module")
def pack_map():
    return load_dictionary_map(PACK / "dictionary_map.yaml")


@pytest.fixture(scope="module")
def pack_dictionary():
    return read_rows(PACK / "substrates" / "dictionary.jsonl", DictionaryRow)


@pytest.fixture(scope="module")
def pack_stats():
    return read_rows(PACK / "substrates" / "univariate_stats.jsonl", StatsRow)


def test_map_refers_only_to_columns_the_dictionary_knows(pack_dictionary):
    """The validator's own map check, run against the shipped pack: a
    join path or format rule naming a column that does not exist is a
    typo the SQL author would inherit."""
    validator = ConformanceValidator(None, None, None, "ig")  # type: ignore[arg-type]
    check = validator._check_dictionary_map(PACK, pack_dictionary)
    assert check.status == "PASS", check.details


def test_review_subsystem_entries_are_human_and_confident(pack_map):
    names = {
        "concepts": {"review report", "supplier response"},
        "metrics": {
            "requested_rate_is_contract_rate",
            "correction_application_rate",
            "corrections_ignored_by_supplier",
            "corrections_ignored_by_auditor",
        },
        "gotchas": {"rate_means_dollars_here", "corrections_never_resubmitted"},
    }
    for section, wanted in names.items():
        entries = {e.name: e for e in getattr(pack_map, section)}
        assert wanted <= set(entries), section
        for name in wanted:
            provenance = entries[name].provenance
            assert provenance is not None and provenance.source == "human", name
            assert provenance.needs_validation is False, name


def test_each_play_session_question_retrieves_its_metric(pack_map):
    for question, metric in PLAY_SESSION_2.items():
        assert [m.name for m in match_metrics(question, pack_map)] == [metric], question


def test_no_data_supplier_question_names_no_metric(pack_map):
    """F1 is grounded by the header rule (NULL over 0) and a where-to-look
    entry, not a metric — a total by supplier is ordinary SQL."""
    question = (
        "Show total invoiced amount per supplier, including suppliers "
        "with no invoice lines."
    )
    assert match_metrics(question, pack_map) == []
    assert any(e.question == question for e in pack_map.examples)


def test_templates_are_lint_silent(pack_map, pack_dictionary):
    """A canonical template the fan-out check challenges would cost every
    answer a repair round and, on the licensed resend, its badge."""
    for metric in pack_map.metrics:
        if metric.template_sql:
            assert lint_fan_out(metric.template_sql, pack_dictionary, pack_map) is None, (
                metric.name
            )


def test_successor_self_join_is_declared_one_to_one(pack_map):
    """A reviewed invoice has at most one successor (UNIQUE supplier_id,
    invoice_number, revision), so the self-join through
    prior_revision_id is exempt from the fan-out check in both
    directions — the application-rate and by-auditor templates need it."""
    path = next(p for p in pack_map.join_paths if p.name == "invoice_to_successor")
    assert path.cardinality == "one_to_one"
    (step,) = path.steps
    assert (step.from_table, step.from_column) == ("invoices", "id")
    assert (step.to_table, step.to_column) == ("invoices", "prior_revision_id")


def test_the_two_readings_are_declared_where_the_session_split_them(pack_map):
    metrics = {m.name: m for m in pack_map.metrics}
    assert [i.name for i in metrics["correction_application_rate"].interpretations] == [
        "per correction",
        "per review",
    ]
    assert [i.name for i in metrics["corrections_ignored_by_auditor"].interpretations] == [
        "ignored corrections",
        "corrections requested",
    ]


def test_rate_gotcha_no_longer_offers_the_joined_count_shape(pack_map):
    """Post-Block-2 S2: the 'COUNT the matches / COUNT the population'
    alternative led into scalar-subquery joined COUNTs that trip the
    fan-out lint. The one-scope SUM(CASE) replaces it."""
    gotcha = next(g for g in pack_map.gotchas if g.name == "rate_needs_unflagged_side")
    assert "COUNT the matches" not in gotcha.detail
    assert "SUM(CASE WHEN <match> THEN 1" in gotcha.detail
    assert "ONE" in gotcha.detail


def test_grounding_renders_the_new_entries(pack_map, pack_dictionary, pack_stats):
    rendered = render_grounding(
        pack_dictionary,
        pack_map,
        pack_stats,
        dialect="duckdb",
        question="How often do suppliers actually apply the corrections we request?",
    )
    head, _, rest = rendered.partition("## Tables and columns")
    assert "metric correction_application_rate" in head
    assert "JOIN invoices succ ON succ.prior_revision_id = inv.id" in head
    assert "- review report (aka corrections we request" in rest
    assert "- rate_means_dollars_here:" in rest
    assert "- invoice_to_successor: invoices.id = invoices.prior_revision_id" in rest
    assert "Q: List audit rejections by reviewer." in rest


def test_templates_never_scale_an_interval(pack_map, pack_dictionary):
    """Duration pass: the interval-arithmetic lint over every canonical
    template — a recommended shape must never meet a lint (the S2
    lesson, where the gotcha's own indicator shape drew the fan-out
    challenge)."""
    from engine.tools.interval_lint import lint_interval_arithmetic

    for metric in pack_map.metrics:
        if metric.template_sql:
            assert lint_interval_arithmetic(metric.template_sql, pack_dictionary) is None, (
                metric.name
            )


def test_time_in_status_gotcha_names_the_unit_shapes(pack_map):
    """Post-coverage W3 rep 4: the gotcha's pairing advice was followed
    and the interval was then divided by 86400. The gotcha now says
    EPOCH first, or DATE_DIFF, and names the wrong shape."""
    gotcha = next(g for g in pack_map.gotchas if g.name == "time_in_status")
    assert "EPOCH(" in gotcha.detail
    assert "DATE_DIFF('hour'" in gotcha.detail
    assert "/ 86400" in gotcha.detail  # the wrong shape, named


def test_rate_gotcha_recommends_the_exists_indicator(pack_map):
    """Post-coverage S2: two reps were warn-capped by fan_out_override on
    the gotcha's OWN recommended shape — a CASE indicator over the
    line-grain LEFT JOIN, which the fan-out check challenges because a
    line may carry more than one finding by schema. The recommended
    shape is now EXISTS, the correction_application_rate template's,
    and never meets the lint; the join stays undeclared (not
    one_to_one — correct by luck is not correct)."""
    gotcha = next(g for g in pack_map.gotchas if g.name == "rate_needs_unflagged_side")
    assert "CASE WHEN EXISTS (SELECT 1 FROM findings f" in gotcha.detail
    assert "IS NOT NULL THEN 1" not in gotcha.detail
    assert "correction_application_rate" in gotcha.detail
    assert not any(
        {s.from_table, s.to_table} == {"invoice_lines", "findings"}
        for path in pack_map.join_paths
        if path.cardinality == "one_to_one"
        for s in path.steps
    )


def test_the_polish_pass_silent_shapes_stay_silent(pack_map, pack_dictionary):
    """Named silent fixtures under the pack's own dictionary and map:
    the flagship table's correlated shape (each aggregate in its own
    scope, the browser's correct resend that used to ship [UNVERIFIED])
    and the correction_application_rate template, whose SUM(CASE WHEN
    NOT EXISTS …) reads no outer column and counts the row grain."""
    from tests.test_tool_sql_lint import (
        AMB1_DISTINCT_CTE,
        AMB1_DISTINCT_CTE_UNALIASED,
        FLAGSHIP_ATTEMPT_2,
        WF_CLOSED_SAVINGS,
    )

    assert lint_fan_out(FLAGSHIP_ATTEMPT_2, pack_dictionary, pack_map) is None
    # Close Pass: the terminal-status sum inside a CTE, vouched by the
    # history path's declared condition; the anti-join to a DISTINCT
    # CTE on its key, vouched by the projection.
    assert lint_fan_out(WF_CLOSED_SAVINGS, pack_dictionary, pack_map) is None
    assert lint_fan_out(AMB1_DISTINCT_CTE, pack_dictionary, pack_map) is None
    assert lint_fan_out(AMB1_DISTINCT_CTE_UNALIASED, pack_dictionary, pack_map) is None
    template = next(
        m for m in pack_map.metrics if m.name == "correction_application_rate"
    )
    assert lint_fan_out(template.template_sql, pack_dictionary, pack_map) is None


def test_savings_realized_reaches_recovered_opportunity(pack_map):
    """Polish Pass, W-F: the play-session question matched only
    rule_savings (through "savings") and the answer named no reading.
    The metric's synonyms are whole-phrase, so the bank's wording must
    contain one of them; "realized" carries this question, and no bare
    "saved" exists to over-match."""
    question = "For each auditor, how much savings have they realized?"
    matched = [m.name for m in match_metrics(question, pack_map)]
    assert "recovered_opportunity" in matched
    assert [m.name for m in match_metrics("How much has each auditor saved?", pack_map)] == [
        "recovered_opportunity"
    ]
    assert match_metrics("Which invoices were saved to the queue?", pack_map) == []
    metric = next(m for m in pack_map.metrics if m.name == "recovered_opportunity")
    assert "saved" not in metric.synonyms
    assert [i.name for i in metric.interpretations] == [
        "closed-invoice opportunity",
        "closed-invoice findings",
        "feedback-authored findings",
    ]


# --- Close Pass: the history path is one-to-one under a declared filter --


def test_history_join_is_conditionally_one_to_one(pack_map):
    """invoice_history -> invoices fans in general and is one row per
    invoice under a terminal status, and for the received transition
    (W3's self-join): declared, never inferred, and executed against
    the world by --check-gold so the fact stays a fact."""
    path = next(p for p in pack_map.join_paths if p.name == "invoices_to_history")
    assert path.cardinality is None
    assert [(c.column, c.values) for c in path.one_to_one_when] == [
        ("invoice_history.to_status", ["CLOSED", "NO_REVIEW_NEEDED"]),
        ("invoice_history.to_status", ["RECEIVED"]),
        ("invoice_history.from_status", ["RECEIVED"]),
    ]
    assert "received once" in path.notes


@pytest.mark.skipif(
    not (PACK / "app.duckdb").is_file(), reason="the pack's world is absent"
)
def test_declared_cardinalities_hold_in_the_world(pack_map):
    """The tripwire, run here as well as under --check-gold: every
    declared condition, at most one row per invoice in the world."""
    from engine.eval.cardinality import check_declared_cardinalities
    from engine.eval.world import World

    checks = check_declared_cardinalities(pack_map, World.from_pack(PACK))
    assert [(c.path, c.status, c.max_per_key) for c in checks] == [
        ("invoices_to_history", "ok", 1)
    ] * 3
    assert all(c.matched_rows > 0 for c in checks)


def test_the_pack_declares_its_entity_kinds(pack_map, pack_dictionary):
    """Backlog Pass: the five kinds a conversation refers back to, every
    column known to the dictionary (the validator test above proves the
    same), every synonym lowercase so the anaphor scan can casefold the
    question once."""
    kinds = {entity.kind: entity for entity in pack_map.entities}
    assert list(kinds) == ["invoice", "supplier", "auditor", "rule", "item"]
    known = {(row.table_name, row.column_name) for row in pack_dictionary}
    for entity in pack_map.entities:
        for qualified in entity.columns:
            table, _, column = qualified.partition(".")
            assert (table, column) in known, qualified
        assert entity.synonyms, entity.kind
        assert all(s == s.lower() for s in entity.synonyms), entity.kind
    assert kinds["invoice"].key_columns == ["invoices.id", "invoices.invoice_number"]
    assert kinds["supplier"].name_columns == ["suppliers.name"]
    assert "invoice_history.actor" in kinds["auditor"].name_columns


def test_rule_labels_are_name_only_by_ruling(pack_map):
    """The ruling-3 amendment: findings.rule_name has 26 distinct values
    — above the enum scan cap, never rendered into the grounding — and
    a first-turn `rule_name = 'duplicate_line'` spelled from the docs is
    correct SQL. Declared as a key it would draw a false ungrounded-key
    challenge; as a name it still anchors "that rule"."""
    rule = next(e for e in pack_map.entities if e.kind == "rule")
    assert rule.key_columns == []
    assert rule.name_columns == ["findings.rule_name", "compliance_rules.rule_code"]
