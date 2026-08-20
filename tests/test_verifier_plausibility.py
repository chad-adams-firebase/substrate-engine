"""Plausibility (§9.3), including phasing acceptance test (b): a
deliberately wrong SQL result trips the check even when the prose is
perfectly faithful — the wrong-but-verified kill shot."""

from engine.config.models import VerifierSettings
from engine.verifier.models import DraftAnswer
from tests.verifier_support import make_verifier, sql_invocation, stats_row

INVOICE_STATS = [
    stats_row("invoices", "id", row_count=161),
    stats_row(
        "invoices",
        "received_at",
        data_type="TIMESTAMP",
        row_count=161,
        min_value="2026-01-02 00:00:00",
        max_value="2026-05-30 00:00:00",
    ),
    stats_row(
        "invoices",
        "total_amount",
        data_type="DOUBLE",
        row_count=161,
        min_value="12.5",
        max_value="98000.0",
    ),
]


def test_acceptance_b_wrong_sql_result_is_refused_despite_faithful_prose():
    """Phasing Phase 4 done-check: the doctored COUNT is faithfully
    drafted, matched exactly — and refused on plausibility."""
    doctored = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoices", [{"n": 1_000_000}]
    )
    verifier, llm = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="how many invoices exist?",
        draft=DraftAnswer(kind="prose", text="There are 1,000,000 invoices."),
        evidence=[doctored],
        attempt=1,
    )
    (claim,) = result.attempt_record.claims
    assert claim.status == "matched_exact"  # faithfulness passed...
    assert result.disposition == "refused"  # ...and it ships anyway? No.
    assert llm.calls == []  # no retries, no judge — refusal is immediate
    (finding,) = result.plausibility
    assert finding.check == "run_sql.count_vs_stats"
    assert finding.severity == "fail"
    assert "1,000,000" in finding.detail and "161" in finding.detail


def test_unfiltered_count_within_tolerance_passes():
    close = sql_invocation("SELECT COUNT(*) AS n FROM invoices", [{"n": 158}])
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 158 invoices."),
        evidence=[close],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_filtered_count_cannot_exceed_the_table():
    impossible = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'open'",
        [{"n": 500}],
    )
    fine = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'open'",
        [{"n": 150}],
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    bad = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="500 are open."),
        evidence=[impossible],
        attempt=1,
    )
    assert bad.disposition == "refused"
    assert bad.plausibility[0].check == "run_sql.filtered_count_bound"

    good = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="150 are open."),
        evidence=[fine],
        attempt=1,
    )
    assert good.plausibility == []


def test_min_max_bounds_skip_aggregates():
    outside = sql_invocation(
        "SELECT total_amount FROM invoices LIMIT 1",
        [{"total_amount": 500_000.0}],
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="One invoice totals $500,000.00."),
        evidence=[outside],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert result.plausibility[0].check == "run_sql.min_max_bounds"

    # SUM legitimately exceeds any single row's bounds: no finding.
    summed = sql_invocation(
        "SELECT SUM(total_amount) AS total_amount FROM invoices",
        [{"total_amount": 500_000.0}],
    )
    assert (
        verifier.verify(
            question="q",
            draft=DraftAnswer(kind="prose", text="They sum to $500,000.00."),
            evidence=[summed],
            attempt=1,
        ).plausibility
        == []
    )


def test_rate_bounds_catch_nonsense_rates():
    nonsense = sql_invocation(
        "SELECT flag_rate FROM t", [{"flag_rate": 146.0}]
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The flag rate is 146.0."),
        evidence=[nonsense],
        attempt=1,
    )
    assert result.plausibility[0].check == "run_sql.rate_bounds"
    assert result.disposition == "refused"

    sane = sql_invocation("SELECT flag_rate FROM t", [{"flag_rate": 0.91}])
    assert (
        verifier.verify(
            question="q",
            draft=DraftAnswer(kind="prose", text="The flag rate is 0.91."),
            evidence=[sane],
            attempt=1,
        ).plausibility
        == []
    )


def test_date_bounds_anchor_to_stats_never_the_wall_clock():
    """The simulated world ends 2026-05-30 and the stats max encodes
    it. A date far past it (e.g. from SQL anchored to the real today)
    fails; slightly past warns and caps at unverified. No
    datetime.now() is consulted anywhere."""
    verifier, _ = make_verifier(stats=INVOICE_STATS)

    far = sql_invocation(
        "SELECT received_at FROM invoices LIMIT 1",
        [{"received_at": "2026-06-15"}],
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Received 2026-06-15."),
        evidence=[far],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert result.plausibility[0].severity == "fail"

    near = sql_invocation(
        "SELECT received_at FROM invoices LIMIT 1",
        [{"received_at": "2026-06-02"}],
    )
    warned = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Received 2026-06-02."),
        evidence=[near],
        attempt=1,
    )
    assert warned.plausibility[0].severity == "warn"
    assert warned.disposition == "unverified"  # clean claims, capped


def test_no_stats_substrate_means_no_plausibility_reference():
    def exploding_stats():
        raise RuntimeError("no stats substrate in this pack")

    from engine.verifier.checks import CheckRegistry, default_checks
    from engine.verifier.verify import Verifier
    from tests.stubs.llm_stub import ScriptedLLM

    verifier = Verifier(
        CheckRegistry(default_checks()),
        ScriptedLLM([]),
        VerifierSettings(),
        exploding_stats,
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 146 findings."),
        evidence=[
            sql_invocation("SELECT COUNT(*) AS n FROM invoices", [{"n": 146}])
        ],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []
