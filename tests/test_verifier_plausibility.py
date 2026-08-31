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


def test_empty_result_caps_at_unverified_never_verified():
    """Fix pass 3 (4b baseline S7): the wrong-question query returned
    an empty table and verified. Now an empty result is a warn — the
    ladder caps at unverified — for a table pass-through whose caption
    carries no claims at all."""
    sql = "SELECT actor FROM invoice_history WHERE from_status = 'X'"
    empty = sql_invocation(sql, [])
    verifier, llm = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=sql),
        evidence=[empty],
        attempt=1,
    )
    assert result.disposition == "unverified"
    assert llm.calls == []
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == ("run_sql.empty_result", "warn")


def test_zero_scalar_caps_at_unverified_and_nonzero_still_verifies():
    """Fix pass 3 (4b baseline S4): 0 where the truth was 114 verified
    — faithfully. A lone zero scalar now ships unverified; the same
    shape with a nonzero cell is untouched."""
    sql = "SELECT COUNT(*) AS n FROM invoices WHERE adjustment_flag = 1"
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    zero = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 0 such invoices."),
        evidence=[sql_invocation(sql, [{"n": 0}])],
        attempt=1,
    )
    assert zero.disposition == "unverified"
    (finding,) = zero.plausibility
    assert finding.check == "run_sql.zero_scalar"
    assert "unverified" in finding.detail

    nonzero = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 114 such invoices."),
        evidence=[sql_invocation(sql, [{"n": 114}])],
        attempt=1,
    )
    assert nonzero.disposition == "verified"
    assert nonzero.plausibility == []

    null_sum = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=sql),
        evidence=[sql_invocation(sql, [{"n": None}])],
        attempt=1,
    )
    assert null_sum.disposition == "unverified"


def test_zero_challenge_is_pack_config():
    settings = VerifierSettings()
    settings.plausibility.challenge_zero_results = False
    verifier, _ = make_verifier(settings=settings, stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Zero."),
        evidence=[sql_invocation("SELECT COUNT(*) AS n FROM invoices WHERE rush_flag = 1", [{"n": 0}])],
        attempt=1,
    )
    assert result.plausibility == []


# --- Play pass: COUNT(DISTINCT) vs distinct_count (R4) ---------------

RULE_STATS = [
    stats_row(
        "compliance_rules",
        "rule_code",
        data_type="VARCHAR",
        row_count=4216,
        distinct_count=10,
    ),
]


def test_count_distinct_compares_to_distinct_count_never_row_count():
    """R4's false refusal: COUNT(DISTINCT rule_code) = 10 was compared
    to the table's 4,216 rows and refused. The comparison target is
    the column's distinct_count."""
    correct = sql_invocation(
        "SELECT COUNT(DISTINCT rule_code) AS n FROM compliance_rules",
        [{"n": 10}],
    )
    verifier, _ = make_verifier(stats=RULE_STATS)
    result = verifier.verify(
        question="how many different rules?",
        draft=DraftAnswer(kind="prose", text="There are 10 distinct rules."),
        evidence=[correct],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_count_distinct_far_from_distinct_count_is_refused():
    doctored = sql_invocation(
        "SELECT COUNT(DISTINCT rule_code) AS n FROM compliance_rules",
        [{"n": 4216}],
    )
    verifier, _ = make_verifier(stats=RULE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 4,216 rules."),
        evidence=[doctored],
        attempt=1,
    )
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.distinct_vs_stats"
    assert "distinct_count" in finding.detail


def test_filtered_count_distinct_is_bounded_not_pinned():
    """A WHERE can legitimately shrink the distinct set — only
    exceeding it is implausible."""
    verifier, _ = make_verifier(stats=RULE_STATS)
    shrunk = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="3 rules."),
        evidence=[
            sql_invocation(
                "SELECT COUNT(DISTINCT rule_code) AS n FROM compliance_rules "
                "WHERE severity = 'CRITICAL'",
                [{"n": 3}],
            )
        ],
        attempt=1,
    )
    assert shrunk.plausibility == []
    inflated = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="80 rules."),
        evidence=[
            sql_invocation(
                "SELECT COUNT(DISTINCT rule_code) AS n FROM compliance_rules "
                "WHERE severity = 'CRITICAL'",
                [{"n": 80}],
            )
        ],
        attempt=1,
    )
    assert inflated.disposition == "refused"
    assert inflated.plausibility[0].check == "run_sql.distinct_vs_stats"


def test_count_distinct_of_an_unknown_column_gets_no_check():
    """No stats row for the column means no reference — never a
    fallback to row_count."""
    verifier, _ = make_verifier(stats=RULE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="7 issuers."),
        evidence=[
            sql_invocation(
                "SELECT COUNT(DISTINCT issuer) AS n FROM compliance_rules",
                [{"n": 7}],
            )
        ],
        attempt=1,
    )
    assert result.plausibility == []


# --- Play pass: the overridden fan-out challenge (W1/W7, P2) ---------


def test_overridden_fan_out_challenge_caps_at_unverified():
    """The licensed resend stays licensed — but an executed statement
    that still trips the lint ships at most [UNVERIFIED], never ✓."""
    fanned = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoices WHERE rush_flag = 1",
        [{"n": 120}],
        final_lint="Fan-out check: COUNT/SUM/AVG over a multi-table join ...",
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="120 rush invoices."),
        evidence=[fanned],
        attempt=1,
    )
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == (
        "run_sql.fan_out_override",
        "warn",
    )
    assert "Fan-out check" in finding.detail


def test_repaired_challenge_leaves_no_trace_on_the_verdict():
    clean = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoices WHERE rush_flag = 1",
        [{"n": 120}],
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="120 rush invoices."),
        evidence=[clean],
        attempt=1,
    )
    assert result.disposition == "verified"


# --- Play pass: aggregate-vs-stats bounds (W1's catch) ---------------

OPPORTUNITY_STATS = [
    stats_row(
        "invoices",
        "opportunity",
        data_type="DOUBLE",
        row_count=100,
        null_rate=0.2,
        mean=50.0,
        min_value="0",
        max_value="900.0",
    ),
    stats_row("suppliers", "id", row_count=40),
]
# cap = mean 50 × 100 rows × 0.8 non-null = 4,000; ×1.1 tolerance = 4,400

FANNED_SUM_SQL = (
    "SELECT s.name AS supplier_name, SUM(i.opportunity) AS total_opportunity "
    "FROM invoices i JOIN suppliers s ON s.id = i.supplier_id GROUP BY s.name"
)


def test_fanned_sum_column_total_exceeding_the_cap_is_refused():
    """W1's exact mechanism: each per-group sum can sit under the
    global cap while the column total betrays the multiplication."""
    fanned = sql_invocation(
        FANNED_SUM_SQL,
        [
            {"supplier_name": "A", "total_opportunity": 3000.0},
            {"supplier_name": "B", "total_opportunity": 2500.0},
        ],
    )
    verifier, _ = make_verifier(stats=OPPORTUNITY_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=FANNED_SUM_SQL),
        evidence=[fanned],
        attempt=1,
    )
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.sum_vs_stats"
    assert finding.severity == "fail"
    assert "sums to 5,500" in finding.detail


def test_sum_cell_beyond_tolerance_fails_within_it_warns():
    verifier, _ = make_verifier(stats=OPPORTUNITY_STATS)
    single = "SELECT SUM(i.opportunity) AS total_opportunity FROM invoices i"
    far = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Total is $5,000.00."),
        evidence=[sql_invocation(single, [{"total_opportunity": 5000.0}])],
        attempt=1,
    )
    assert far.disposition == "refused"
    assert far.plausibility[0].severity == "fail"

    near = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Total is $4,100.00."),
        evidence=[sql_invocation(single, [{"total_opportunity": 4100.0}])],
        attempt=1,
    )
    assert near.disposition == "unverified"  # warn band: stats staleness
    assert near.plausibility[0].severity == "warn"

    under = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Total is $3,900.00."),
        evidence=[sql_invocation(single, [{"total_opportunity": 3900.0}])],
        attempt=1,
    )
    assert under.plausibility == []


def test_sum_of_coalesce_resolves_like_the_bare_column():
    """W1 wrote SUM(COALESCE(i.opportunity, 0)) — COALESCE with a
    literal does not change a sum, so the cap still applies."""
    sql = (
        "SELECT SUM(COALESCE(i.opportunity, 0)) AS total_opportunity "
        "FROM invoices i"
    )
    verifier, _ = make_verifier(stats=OPPORTUNITY_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Total is $20,000.00."),
        evidence=[sql_invocation(sql, [{"total_opportunity": 20000.0}])],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert result.plausibility[0].check == "run_sql.sum_vs_stats"


def test_signed_columns_get_no_sum_cap():
    """A filtered subset of a column with negatives can legitimately
    exceed the whole column's sum — the guard is mandatory."""
    signed = [
        stats_row(
            "adjustments",
            "delta",
            data_type="DOUBLE",
            row_count=100,
            mean=5.0,
            min_value="-250.0",
            max_value="400.0",
        ),
    ]
    verifier, _ = make_verifier(stats=signed)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Positive deltas sum to 90,000."),
        evidence=[
            sql_invocation(
                "SELECT SUM(a.delta) AS positive_total FROM adjustments a "
                "WHERE a.delta > 0",
                [{"positive_total": 90000.0}],
            )
        ],
        attempt=1,
    )
    assert result.plausibility == []


def test_avg_outside_the_column_range_is_refused():
    """No subset's average can leave [min, max]: a violation means the
    wrong column fed the AVG (W6's family, caught when it strays)."""
    sql = (
        "SELECT f.rule_name AS rule_name, AVG(i.opportunity) AS avg_opportunity "
        "FROM findings f JOIN invoices i ON f.invoice_id = i.id "
        "GROUP BY f.rule_name"
    )
    verifier, _ = make_verifier(stats=OPPORTUNITY_STATS)
    far = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=sql),
        evidence=[
            sql_invocation(
                sql, [{"rule_name": "x", "avg_opportunity": 2000.0}]
            )
        ],
        attempt=1,
    )
    assert far.disposition == "refused"
    assert far.plausibility[0].check == "run_sql.avg_vs_stats"

    near = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=sql),
        evidence=[
            sql_invocation(sql, [{"rule_name": "x", "avg_opportunity": 950.0}])
        ],
        attempt=1,
    )
    assert near.disposition == "unverified"  # within tolerance of the span
    assert near.plausibility[0].severity == "warn"

    inside = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=sql),
        evidence=[
            sql_invocation(sql, [{"rule_name": "x", "avg_opportunity": 120.0}])
        ],
        attempt=1,
    )
    assert inside.plausibility == []


def test_renamed_passthrough_columns_face_the_cell_bounds():
    """An alias used to escape min/max and date checks entirely; the
    select-list parse closes that door."""
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Received 2026-08-15."),
        evidence=[
            sql_invocation(
                "SELECT received_at AS day FROM invoices LIMIT 1",
                [{"day": "2026-08-15"}],
            )
        ],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert result.plausibility[0].check == "run_sql.date_bounds"


def test_aggregate_bounds_are_pack_config():
    settings = VerifierSettings()
    settings.plausibility.enforce_aggregate_bounds = False
    verifier, _ = make_verifier(settings=settings, stats=OPPORTUNITY_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="Total is $20,000.00."),
        evidence=[
            sql_invocation(
                "SELECT SUM(i.opportunity) AS total_opportunity FROM invoices i",
                [{"total_opportunity": 20000.0}],
            )
        ],
        attempt=1,
    )
    assert result.plausibility == []
