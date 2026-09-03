"""Plausibility (§9.3), including phasing acceptance test (b): a
deliberately wrong SQL result trips the check even when the prose is
perfectly faithful — the wrong-but-verified kill shot."""

from engine.config.models import VerifierSettings
from engine.tools.envelope import ColumnFormat
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


FLAG_RATE = {"flag_rate": ColumnFormat(kind="rate", scale="fraction")}
FLAG_PCT = {"flag_pct": ColumnFormat(kind="rate", scale="percent")}


def test_rate_bounds_catch_nonsense_rates():
    nonsense = sql_invocation(
        "SELECT flag_rate FROM t", [{"flag_rate": 146.0}], column_formats=FLAG_RATE
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

    sane = sql_invocation(
        "SELECT flag_rate FROM t", [{"flag_rate": 0.91}], column_formats=FLAG_RATE
    )
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


# --- Polish Pass: a count is what the parse says it is -----------------


def test_a_ratio_of_counts_is_not_a_count():
    """The live refusal: "How many invoices do we receive per day on
    average?" drafted as COUNT(*) / COUNT(DISTINCT DATE(received_at)),
    gold-exact at 30.6, refused as "COUNT over invoices returned 30.6;
    stats row_count is 161". A ratio is not a count; the select-list
    parse says so (DATE(...) even leaves it Opaque), and no row_count
    comparison applies."""
    per_day = sql_invocation(
        "SELECT COUNT(*) * 1.0 / COUNT(DISTINCT DATE(received_at)) AS per_day "
        "FROM invoices",
        [{"per_day": 30.615384615384617}],
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="How many invoices do we receive per day on average?",
        draft=DraftAnswer(kind="prose", text="About 30.62 invoices per day."),
        evidence=[per_day],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_a_subquery_count_inside_a_ratio_draws_neither_count_check():
    """NP4's shape over one table used to satisfy both the plain and
    the DISTINCT regex through the scalar subquery's SELECT COUNT(*)."""
    rate = sql_invocation(
        "SELECT COUNT(DISTINCT id) * 1.0 / (SELECT COUNT(*) FROM invoices) AS r "
        "FROM invoices WHERE status = 'open'",
        [{"r": 0.42}],
    )
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="42% are open."),
        evidence=[rate],
        attempt=1,
    )
    assert result.plausibility == []


def test_arithmetic_over_a_count_is_not_bounded():
    """Accepted cost of classifying by the parse: COUNT(*) + 0 is an
    Arith, not a count, so the row_count pin does not read it."""
    padded = sql_invocation("SELECT COUNT(*) + 0 AS n FROM invoices", [{"n": 1_000_000}])
    verifier, _ = make_verifier(stats=INVOICE_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 1,000,000 invoices."),
        evidence=[padded],
        attempt=1,
    )
    assert [f.check for f in result.plausibility] == []


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


# --- Pin pass: the joined-count bound (MT2's backstop) ----------------

JOINED_STATS = [
    stats_row("findings", "id", row_count=6042),
    stats_row("compliance_rules", "id", row_count=4216),
    stats_row("invoices", "id", row_count=1990),
    stats_row("invoice_lines", "id", row_count=9648),
]

MT2_EXPRESSION_SQL = (
    "SELECT COUNT(*) AS critical_compliance_findings FROM findings f "
    "JOIN compliance_rules cr "
    "ON f.rule_name = CONCAT('compliance_', cr.rule_code) "
    "WHERE cr.severity = 'CRITICAL'"
)


def test_joined_count_past_the_fail_factor_is_refused():
    """MT2's breach shape: 107,509 over a 6,042-row largest queried
    table is 17.8× — the single-table count checks skip joins by
    design, and this is their backstop."""
    fanned = sql_invocation(
        MT2_EXPRESSION_SQL, [{"critical_compliance_findings": 107509}]
    )
    verifier, _ = make_verifier(stats=JOINED_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 107,509 findings."),
        evidence=[fanned],
        attempt=1,
    )
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.joined_count_vs_stats"
    assert finding.severity == "fail"
    assert "17.8×" in finding.detail
    assert "findings" in finding.detail


def test_joined_count_between_the_factors_warns_to_unverified():
    modest = sql_invocation(
        MT2_EXPRESSION_SQL, [{"critical_compliance_findings": 10000}]
    )
    verifier, _ = make_verifier(stats=JOINED_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 10,000 findings."),
        evidence=[modest],
        attempt=1,
    )
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.joined_count_vs_stats"
    assert finding.severity == "warn"


def test_honest_line_grain_join_at_exactly_one_x_passes():
    """A count at the grain of the largest queried table is the honest
    ceiling — the bound is strictly-greater, so 1.0× ships verified."""
    line_grain = sql_invocation(
        "SELECT COUNT(*) AS n FROM invoice_lines l "
        "JOIN invoices i ON l.invoice_id = i.id",
        [{"n": 9648}],
    )
    verifier, _ = make_verifier(stats=JOINED_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 9,648 lines."),
        evidence=[line_grain],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_joined_count_over_unresolvable_names_skips_the_bound():
    """CTE and subquery names have no stats row: nothing to bound with,
    so the check stands down — the known-open gap the residuals doc
    records rather than a false alarm."""
    cte = sql_invocation(
        "SELECT COUNT(*) AS n FROM cte_a JOIN cte_b ON cte_a.x = cte_b.x",
        [{"n": 999999}],
    )
    verifier, _ = make_verifier(stats=JOINED_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 999,999."),
        evidence=[cte],
        attempt=1,
    )
    assert result.plausibility == []


def test_grouped_count_column_sum_is_bounded_too():
    grouped = sql_invocation(
        "SELECT cr.severity, COUNT(*) AS n FROM findings f "
        "JOIN compliance_rules cr "
        "ON f.rule_name = CONCAT('compliance_', cr.rule_code) "
        "GROUP BY cr.severity",
        [{"severity": "HIGH", "n": 50000}, {"severity": "LOW", "n": 60000}],
    )
    verifier, _ = make_verifier(stats=JOINED_STATS)
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(
            kind="prose", text="HIGH has 50,000 and LOW has 60,000."
        ),
        evidence=[grouped],
        attempt=1,
    )
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.joined_count_vs_stats"
    assert finding.severity == "fail"
    assert "sums to" in finding.detail


def test_joined_count_bound_can_be_configured_off():
    from engine.config.models import PlausibilitySettings

    fanned = sql_invocation(
        MT2_EXPRESSION_SQL, [{"critical_compliance_findings": 107509}]
    )
    verifier, _ = make_verifier(
        settings=VerifierSettings(
            plausibility=PlausibilitySettings(enforce_joined_count_bound=False)
        ),
        stats=JOINED_STATS,
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There are 107,509 findings."),
        evidence=[fanned],
        attempt=1,
    )
    assert result.plausibility == []


# --- Pin pass: saturated rates (S2's backstop) ------------------------

FRACTION_RATE = {"item_flag_rate": ColumnFormat(kind="rate", scale="fraction")}
S2_SQL = (
    "SELECT AVG(flagged.is_flagged) AS item_flag_rate FROM invoice_lines l "
    "LEFT JOIN (SELECT invoice_id, line_number, 1.0 AS is_flagged "
    "FROM findings) flagged ON l.invoice_id = flagged.invoice_id"
)


def test_saturated_rate_ships_unverified_never_refused():
    """S2's breach shape: exactly 1.0 with no basis column in sight.
    A legitimate 100% ships the same way — [UNVERIFIED], not refused;
    the warn only takes the badge off the suspicious case."""
    saturated = sql_invocation(S2_SQL, [{"item_flag_rate": 1.0}], column_formats=FRACTION_RATE)
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The rate is 1.0."),
        evidence=[saturated],
        attempt=1,
    )
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.rate_saturated"
    assert finding.severity == "warn"


def test_saturated_zero_rate_draws_the_zero_challenge_too():
    """Two warns, one verdict: the lone 0.0 scalar is both a zero
    result and a saturated rate — still just [UNVERIFIED]."""
    zeroed = sql_invocation(S2_SQL, [{"item_flag_rate": 0.0}], column_formats=FRACTION_RATE)
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The rate is 0.0."),
        evidence=[zeroed],
        attempt=1,
    )
    assert result.disposition == "unverified"
    checks = {finding.check for finding in result.plausibility}
    assert "run_sql.rate_saturated" in checks
    assert "run_sql.zero_scalar" in checks


def test_small_basis_beside_the_rate_suppresses_the_warn():
    """Five lines all flagged is an honest 1.0 — a count cell in the
    same row below the minimum basis stands the check down."""
    tiny = sql_invocation(
        S2_SQL.replace("AVG", "COUNT(*) AS line_count, AVG"),
        [{"line_count": 5, "item_flag_rate": 1.0}],
        column_formats=FRACTION_RATE,
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="All 5 lines: rate 1.0."),
        evidence=[tiny],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_large_basis_beside_the_rate_still_warns():
    sized = sql_invocation(
        S2_SQL.replace("AVG", "COUNT(*) AS line_count, AVG"),
        [{"line_count": 66, "item_flag_rate": 1.0}],
        column_formats=FRACTION_RATE,
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="All 66 lines: rate 1.0."),
        evidence=[sized],
        attempt=1,
    )
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.rate_saturated"


def test_unsaturated_rate_stays_silent():
    honest = sql_invocation(S2_SQL, [{"item_flag_rate": 0.9545}], column_formats=FRACTION_RATE)
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The rate is 0.9545."),
        evidence=[honest],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.plausibility == []


def test_saturated_rate_check_can_be_configured_off():
    from engine.config.models import PlausibilitySettings

    saturated = sql_invocation(S2_SQL, [{"item_flag_rate": 1.0}], column_formats=FRACTION_RATE)
    verifier, _ = make_verifier(
        settings=VerifierSettings(
            plausibility=PlausibilitySettings(challenge_saturated_rates=False)
        ),
        stats=[],
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The rate is 1.0."),
        evidence=[saturated],
        attempt=1,
    )
    assert result.plausibility == []


# --- Rate scale (the coverage pass): the bound holds at the scale the
# renderer shows, and a fraction hiding in a percent alias loses its badge.


def test_rate_bounds_hold_at_the_hints_scale():
    """A fanned 1.0476 on a fraction column is outside [0, 1] and refused
    — it used to pass as 'under 100'. A percent column carries 92.21
    without complaint, and 146 on it fails the 0–100 bound."""
    fanned = sql_invocation(
        "SELECT flag_rate FROM t", [{"flag_rate": 1.0476190476190477}], column_formats=FLAG_RATE
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The rate is 1.0476190476190477."),
        evidence=[fanned],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert result.plausibility[0].check == "run_sql.rate_bounds"
    assert "[0,1]" in result.plausibility[0].detail

    percent = sql_invocation(
        "SELECT flag_pct FROM t", [{"flag_pct": 92.21}], column_formats=FLAG_PCT
    )
    assert verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="92.21 percent."),
        evidence=[percent],
        attempt=1,
    ).plausibility == []

    over = sql_invocation(
        "SELECT flag_pct FROM t", [{"flag_pct": 146.0}], column_formats=FLAG_PCT
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="146 percent."),
        evidence=[over],
        attempt=1,
    )
    assert result.disposition == "refused"
    assert "[0,100]" in result.plausibility[0].detail


def test_a_percent_column_saturates_at_one_hundred():
    saturated = sql_invocation(
        "SELECT COUNT(*) AS line_count, flag_pct FROM t",
        [{"line_count": 66, "flag_pct": 100.0}],
        column_formats=FLAG_PCT,
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="All 66 lines: 100.0 percent."),
        evidence=[saturated],
        attempt=1,
    )
    assert result.disposition == "unverified"
    assert [f.check for f in result.plausibility] == ["run_sql.rate_saturated"]


def test_an_unhinted_rate_named_column_gets_no_rate_check():
    """No display.rate block, no rate hint, no bound: the hint is the
    one resolution both the renderer and the verifier read."""
    unhinted = sql_invocation("SELECT flag_rate FROM t", [{"flag_rate": 146.0}])
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The flag rate is 146.0."),
        evidence=[unhinted],
        attempt=1,
    )
    assert result.disposition == "verified"


def test_a_fraction_written_into_a_percent_alias_loses_the_badge():
    """ROUND(x, 2) AS flag_pct over a 0–1 x renders 0.9% for a true 92%
    and sits inside the 0–100 bound — the scale decision's one knowingly
    wrong case, warn-capped: every value at or below 1.0 across rows."""
    suspect = sql_invocation(
        "SELECT s.name AS supplier_name, flag_pct FROM t",
        [
            {"supplier_name": "RVX01", "flag_pct": 0.9221},
            {"supplier_name": "ACME", "flag_pct": 0.5},
        ],
        column_formats=FLAG_PCT,
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="RVX01 0.9221, ACME 0.5."),
        evidence=[suspect],
        attempt=1,
    )
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.rate_scale_suspect"
    assert finding.severity == "warn"
    assert "92.21%" in finding.detail

    # A lone cell at or below 1.0 that is not exactly 0 or 1 is suspect
    # too; a lone 1.0 or 0.0 is the saturation check's case instead.
    lone = sql_invocation(
        "SELECT flag_pct FROM t", [{"flag_pct": 0.75}], column_formats=FLAG_PCT
    )
    result = verifier.verify(
        question="q", draft=DraftAnswer(kind="prose", text="0.75."), evidence=[lone], attempt=1
    )
    assert [f.check for f in result.plausibility] == ["run_sql.rate_scale_suspect"]
    one = sql_invocation(
        "SELECT COUNT(*) AS n, flag_pct FROM t",
        [{"n": 66, "flag_pct": 1.0}],
        column_formats=FLAG_PCT,
    )
    result = verifier.verify(
        question="q", draft=DraftAnswer(kind="prose", text="66 and 1.0."), evidence=[one], attempt=1
    )
    assert "run_sql.rate_scale_suspect" not in {f.check for f in result.plausibility}

    # Real percents stay silent.
    honest = sql_invocation(
        "SELECT s.name AS supplier_name, flag_pct FROM t",
        [{"supplier_name": "RVX01", "flag_pct": 92.21}, {"supplier_name": "ACME", "flag_pct": 0.5}],
        column_formats=FLAG_PCT,
    )
    assert verifier.verify(
        question="q", draft=DraftAnswer(kind="prose", text="92.21 and 0.5."), evidence=[honest], attempt=1
    ).plausibility == []


def test_overridden_enum_challenge_warns_with_its_reason():
    """R-A's shape: an empty result from a value the column never holds
    ships [UNVERIFIED] for a STATED reason, beside the generic empty-
    result warn — never verified."""
    empty = sql_invocation(
        "SELECT actor, COUNT(*) AS n FROM invoice_history WHERE to_status = 'REJECTED' GROUP BY actor",
        [],
        final_enum_lint="Enum check: `invoice_history.to_status` never takes 'REJECTED' in this data",
    )
    verifier, _ = make_verifier(stats=[])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="No rejections were recorded."),
        evidence=[empty],
        attempt=1,
    )
    assert result.disposition == "unverified"
    checks = {finding.check: finding for finding in result.plausibility}
    assert "run_sql.enum_literal_override" in checks
    assert "run_sql.empty_result" in checks
    assert "never takes 'REJECTED'" in checks["run_sql.enum_literal_override"].detail
    assert checks["run_sql.enum_literal_override"].severity == "warn"


# --- Duration pass: the duration class's floor and ceiling (W3) -------

from engine.config.models import PlausibilitySettings  # noqa: E402
from tests.verifier_support import W3_REP4_SQL  # noqa: E402

DAYS = {"avg_time_in_days": ColumnFormat(kind="duration", unit="days")}
HOURS = {"avg_hours": ColumnFormat(kind="duration", unit="hours")}
CLOCK = {"avg_wait": ColumnFormat(kind="duration")}
# invoice_history.at spans 88.4 days in the seed-42 world.
HISTORY_STATS = [
    stats_row("invoice_history", "invoice_id", row_count=6000),
    stats_row(
        "invoice_history",
        "at",
        data_type="TIMESTAMP",
        row_count=6000,
        min_value="2026-03-02T08:00:00",
        max_value="2026-05-29T18:00:00",
    ),
]
PAIRED = (
    "SELECT {select} FROM invoices i "
    "JOIN invoice_history h ON h.invoice_id = i.id"
)


def _verify(invocation, stats, settings=None):
    verifier, _ = make_verifier(stats=stats, settings=settings)
    return verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="The average is shown."),
        evidence=[invocation],
        attempt=1,
    )


def test_the_post_coverage_w3_cell_ships_unverified_never_refused():
    """W3 rep 4's exact table: AVG(interval) / 86400 returned the clock
    string 0:00:00.041667 under a days hint — 0.041667 seconds, an
    aggregate below one second. The floor takes the badge off; the
    zero challenge never saw it (a string is not 0)."""
    rep4 = sql_invocation(
        W3_REP4_SQL, [{"avg_time_in_days": "0:00:00.041667"}], column_formats=DAYS
    )
    result = _verify(rep4, HISTORY_STATS)
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == ("run_sql.duration_degenerate", "warn")
    assert "0:00:00.041667" in finding.detail


def test_a_small_basis_beside_a_zero_duration_stands_the_floor_down():
    """Twelve invoices scored the instant they arrived is an honest
    zero — a count cell in the same row under the basis suppresses the
    warn; a count over the basis does not."""
    sql = PAIRED.format(select="COUNT(*) AS invoice_count, AVG(h.at - i.received_at) AS avg_wait")
    honest = sql_invocation(
        sql, [{"invoice_count": 12, "avg_wait": "0:00:00"}], column_formats=CLOCK
    )
    assert _verify(honest, []).disposition == "verified"

    large = sql_invocation(
        sql, [{"invoice_count": 400, "avg_wait": "0:00:00"}], column_formats=CLOCK
    )
    result = _verify(large, [])
    assert result.disposition == "unverified"
    assert {f.check for f in result.plausibility} == {"run_sql.duration_degenerate"}


def test_a_listing_of_zero_durations_is_not_degenerate():
    """The floor is for aggregates: a per-invoice listing may hold
    zeros legitimately, and a lone plain cell is the zero challenge's
    business, not this one's."""
    listing = sql_invocation(
        "SELECT i.invoice_number, i.scored_at - i.received_at AS wait FROM invoices i",
        [{"invoice_number": "A-1", "wait": "0:00:00"}, {"invoice_number": "A-2", "wait": "0:00:00"}],
        column_formats={"wait": ColumnFormat(kind="duration")},
    )
    assert _verify(listing, []).disposition == "verified"


def test_the_epoch_form_reads_its_aggregate_structurally_and_a_case_wrapper_lexically():
    """The recommended EPOCH-first shape is numeric to the parse (guard
    pass), so the floor sees the AVG in the tree; the original
    play-session W3 (self-subtraction, exactly 0) comes through it
    warned. What the parse still declines — a CASE wrapper — is read
    lexically, a warn either way, so the cost of a false read is a
    badge."""
    epoch = sql_invocation(
        PAIRED.format(select="AVG(EPOCH(h.at - i.received_at)) / 3600.0 AS avg_hours, COUNT(*) AS n"),
        [{"avg_hours": 0.0, "n": 1983}],
        column_formats=HOURS,
    )
    result = _verify(epoch, [])
    assert result.disposition == "unverified"
    assert {f.check for f in result.plausibility} == {"run_sql.duration_degenerate"}

    wrapped = sql_invocation(
        PAIRED.format(
            select="AVG(CASE WHEN h.at > i.received_at THEN EPOCH(h.at - i.received_at) END) "
            "/ 3600.0 AS avg_hours, COUNT(*) AS n"
        ),
        [{"avg_hours": 0.0, "n": 1983}],
        column_formats=HOURS,
    )
    result = _verify(wrapped, [])
    assert result.disposition == "unverified"
    assert {f.check for f in result.plausibility} == {"run_sql.duration_degenerate"}


def test_an_average_past_the_data_span_is_refused():
    """The ceiling: an average wait of 200 days in data whose timestamps
    span 88 cannot be — the parse sees the AVG, so this fails."""
    too_long = sql_invocation(
        PAIRED.format(select="AVG(h.at - i.received_at) AS avg_wait"),
        [{"avg_wait": "4800:00:00"}],
        column_formats=CLOCK,
    )
    result = _verify(too_long, HISTORY_STATS)
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == ("run_sql.duration_span_bound", "fail")
    assert "200 days" in finding.detail and "88.4" in finding.detail


def test_a_sum_may_exceed_the_span_in_either_shape():
    total = sql_invocation(
        PAIRED.format(select="SUM(h.at - i.received_at) AS total_wait"),
        [{"total_wait": "4800:00:00"}],
        column_formats={"total_wait": ColumnFormat(kind="duration")},
    )
    assert _verify(total, HISTORY_STATS).disposition == "verified"
    # The gotcha's unit-counting shape, summed: the parse sees the SUM
    # through DATE_DIFF (guard pass), so the ceiling stands down.
    units = sql_invocation(
        PAIRED.format(select="SUM(DATE_DIFF('hour', i.received_at, h.at)) AS total_hours"),
        [{"total_hours": 4800.0}],
        column_formats={"total_hours": ColumnFormat(kind="duration", unit="hours")},
    )
    assert _verify(units, HISTORY_STATS).disposition == "verified"


def test_the_epoch_average_past_the_span_refuses_and_a_case_wrapper_only_warns():
    """Guard pass: the recommended shape must never be the shape the
    guards cannot read. AVG(EPOCH(...)) / 3600 past the data's span is
    an AVG to the parse now and fails; a CASE-wrapped item the parse
    still declines warns, since it cannot rule out a SUM there."""
    epoch = sql_invocation(
        PAIRED.format(select="AVG(EPOCH(h.at - i.received_at)) / 3600.0 AS avg_hours"),
        [{"avg_hours": 4800.0}],
        column_formats=HOURS,
    )
    result = _verify(epoch, HISTORY_STATS)
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == ("run_sql.duration_span_bound", "fail")
    assert "cannot classify" not in finding.detail
    # Without timestamp stats there is no span to bound against.
    assert _verify(epoch, []).disposition == "verified"

    wrapped = sql_invocation(
        PAIRED.format(
            select="AVG(CASE WHEN h.at > i.received_at THEN EPOCH(h.at - i.received_at) END) "
            "/ 3600.0 AS avg_hours"
        ),
        [{"avg_hours": 4800.0}],
        column_formats=HOURS,
    )
    result = _verify(wrapped, HISTORY_STATS)
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert (finding.check, finding.severity) == ("run_sql.duration_span_bound", "warn")
    assert "cannot classify" in finding.detail


def test_duration_bounds_are_pack_knobs():
    off = VerifierSettings(
        plausibility=PlausibilitySettings(
            challenge_degenerate_durations=False, enforce_duration_span_bound=False
        )
    )
    rep4 = sql_invocation(
        W3_REP4_SQL, [{"avg_time_in_days": "0:00:00.041667"}], column_formats=DAYS
    )
    assert _verify(rep4, HISTORY_STATS, off).disposition == "verified"
    too_long = sql_invocation(
        PAIRED.format(select="AVG(h.at - i.received_at) AS avg_wait"),
        [{"avg_wait": "4800:00:00"}],
        column_formats=CLOCK,
    )
    assert _verify(too_long, HISTORY_STATS, off).disposition == "verified"


def test_overridden_interval_challenge_warns_with_its_reason():
    """The licensed resend still executes; the recorded challenge costs
    the badge for a stated reason, beside the floor's own warn."""
    overridden = sql_invocation(
        W3_REP4_SQL,
        [{"avg_time_in_days": "0:00:00.041667"}],
        final_interval_lint="Interval-arithmetic check: `avg_time_in_days` scales a timestamp difference",
        column_formats=DAYS,
    )
    result = _verify(overridden, HISTORY_STATS)
    assert result.disposition == "unverified"
    checks = {f.check: f for f in result.plausibility}
    assert checks["run_sql.interval_arithmetic_override"].severity == "warn"
    assert "scales a timestamp difference" in checks["run_sql.interval_arithmetic_override"].detail
    assert "run_sql.duration_degenerate" in checks

    repaired = sql_invocation(
        PAIRED.format(select="AVG(EPOCH(h.at - i.received_at)) / 3600.0 AS avg_hours"),
        [{"avg_hours": 1.0}],
        column_formats=HOURS,
    )
    assert _verify(repaired, HISTORY_STATS).disposition == "verified"


# --- The entity-count bound (guard pass, AMB2) ---------------------------

# The post-duration AMB2 rep 1, attempt 2: the enum challenge's pointer
# at invoice_history read as an instruction; 6,432 transitions shipped
# verified as an invoice count. Rep 2/3's three-status list gave 5,199.
AMB2_HISTORY_SQL = (
    "SELECT \n    COUNT(*) AS invoice_count\nFROM \n    invoice_history\nWHERE \n"
    "    to_status IN ('RECEIVED', 'READY', 'CLAIMED', 'IN_REVIEW')"
)
ENTITY_STATS = [
    stats_row("invoices", "id", row_count=1990),
    stats_row("invoice_history", "id", row_count=8345),
    stats_row("suppliers", "id", row_count=40),
    stats_row("findings", "id", row_count=6042),
    stats_row("invoice_lines", "id", row_count=9648),
]


def test_amb2s_history_count_aliased_as_invoices_ships_unverified():
    """The mechanism the guard pass closes: a filtered single-table count
    under invoice_history's own row_count passes every other bound;
    the alias's noun is the only thing that says 6,432 invoices cannot
    be. Warn, so [UNVERIFIED] rather than refused."""
    for counted in (6432, 5199):
        walked = sql_invocation(AMB2_HISTORY_SQL, [{"invoice_count": counted}])
        result = _verify(walked, ENTITY_STATS)
        assert result.disposition == "unverified", counted
        (finding,) = result.plausibility
        assert (finding.check, finding.severity) == (
            "run_sql.entity_count_exceeds_table", "warn"
        )
        assert "1,990 invoices rows" in finding.detail
        assert "the statement reads invoice_history" in finding.detail


def test_a_legitimate_invoice_count_stays_silent():
    """AMB2's attempt 1 (78 of 1,990) and REC-SQL's total (exactly 1,990,
    aliased total_invoices) are within the table; the row_count
    tolerance covers a trailing snapshot."""
    fine = sql_invocation(
        "SELECT COUNT(*) AS invoice_count FROM invoices WHERE status = 'READY'",
        [{"invoice_count": 78}],
    )
    assert _verify(fine, ENTITY_STATS).disposition == "verified"
    total = sql_invocation(
        "SELECT COUNT(*) AS total_invoices FROM invoices", [{"total_invoices": 1990}]
    )
    assert _verify(total, ENTITY_STATS).disposition == "verified"
    trailing = sql_invocation(
        "SELECT COUNT(DISTINCT h.invoice_id) AS invoice_count FROM invoice_history h",
        [{"invoice_count": 2100}],  # within the 10% tolerance
    )
    assert _verify(trailing, ENTITY_STATS).disposition == "verified"


def test_a_grouped_count_column_sums_against_the_entity_table():
    grouped = sql_invocation(
        "SELECT to_status, COUNT(*) AS invoice_count FROM invoice_history "
        "GROUP BY to_status ORDER BY to_status",
        [{"to_status": "CLOSED", "invoice_count": 3000},
         {"to_status": "READY", "invoice_count": 3000}],
    )
    result = _verify(grouped, ENTITY_STATS)
    assert result.disposition == "unverified"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.entity_count_exceeds_table"
    assert "sums to 6,000" in finding.detail
    # A partition of the entity sums to at most the table: silent.
    partition = sql_invocation(
        "SELECT status, COUNT(*) AS invoice_count FROM invoices GROUP BY status ORDER BY status",
        [{"status": "CLOSED", "invoice_count": 1200},
         {"status": "READY", "invoice_count": 78}],
    )
    assert _verify(partition, ENTITY_STATS).disposition == "verified"


def test_a_count_through_a_cte_resolves_to_its_alias():
    """The parse follows the CTE, so hoisting the count does not hide
    it — and a fanning join aliased as an entity count warns beside
    the joined-count bound rather than instead of it."""
    hoisted = sql_invocation(
        "WITH c AS (SELECT COUNT(*) AS invoice_count FROM invoice_history) "
        "SELECT invoice_count FROM c",
        [{"invoice_count": 8345}],
    )
    result = _verify(hoisted, ENTITY_STATS)
    assert result.disposition == "unverified"
    assert {f.check for f in result.plausibility} == {"run_sql.entity_count_exceeds_table"}
    fanned = sql_invocation(
        "SELECT COUNT(*) AS invoice_count FROM invoices i "
        "JOIN invoice_lines l ON l.invoice_id = i.id",
        [{"invoice_count": 9648}],
    )
    result = _verify(fanned, ENTITY_STATS)
    assert result.disposition == "unverified"
    # 9,648 is exactly 1.0× the largest queried table, under the
    # joined-count warn factor — only the entity bound sees it.
    assert {f.check for f in result.plausibility} == {"run_sql.entity_count_exceeds_table"}


def test_aliases_that_make_no_entity_claim_are_silent():
    """No count affix, or a noun no stats table spells: nothing to
    bound against, however large the number."""
    # Values sit inside every other bound (under invoice_history's
    # 8,345) and past invoices' 1,990, so only an entity claim could
    # object — and none of these aliases makes one.
    for sql, rows in (
        ("SELECT COUNT(*) AS credit_memo_count FROM invoice_history WHERE actor = 'x'", [{"credit_memo_count": 5000}]),
        ("SELECT COUNT(*) AS rules_seen FROM invoice_history WHERE actor = 'x'", [{"rules_seen": 5000}]),
        ("SELECT COUNT(*) AS n FROM invoice_history", [{"n": 8000}]),
        # A SUM aliased like a count is not a count to the parse.
        ("SELECT SUM(amount) AS invoice_count FROM invoice_history", [{"invoice_count": 99999}]),
    ):
        assert _verify(sql_invocation(sql, rows), ENTITY_STATS).disposition == "verified", sql


def test_entity_count_bound_is_a_pack_knob():
    from engine.config.models import PlausibilitySettings

    off = VerifierSettings(
        plausibility=PlausibilitySettings(enforce_entity_count_bound=False)
    )
    walked = sql_invocation(AMB2_HISTORY_SQL, [{"invoice_count": 6432}])
    assert _verify(walked, ENTITY_STATS, off).disposition == "verified"


def test_the_alias_noun_rule_on_the_last_runs_aliases():
    """Every count alias the post-duration report produced, and what
    the stem rule makes of it against the InvoiceGuard stats tables."""
    from engine.verifier.checks.run_sql import entity_table_for_alias

    tables = [
        "compliance_reports", "compliance_rules", "config", "contracts",
        "finding_feedback", "findings", "invoice_history", "invoice_lines",
        "invoices", "review_report_lines", "review_reports",
        "scheduled_tasks", "suppliers", "users",
    ]
    resolves = {
        "invoice_count": "invoices",
        "total_invoices": "invoices",
        "findings_count": "findings",
        "critical_finding_count": "findings",
        "invoice_line_count": "invoice_lines",
        "total_invoice_line_count": "invoice_lines",
        "n_suppliers": "suppliers",
        "number_of_users": "users",
        "count_of_contracts": "contracts",
        "invoice_history_count": "invoice_history",
        "Invoice_Count": "invoices",
    }
    for alias, table in resolves.items():
        assert entity_table_for_alias(alias, tables) == table, alias
    silent = [
        "credit_memo_count", "revision_count", "fire_count",
        "rate_variance_count", "ready_backlog_count", "rules_seen",
        "unreviewed_invoices", "invoices_with_findings", "report_count",
        "rule_count", "count", "total", "n_", "_count",
    ]
    for alias in silent:
        assert entity_table_for_alias(alias, tables) is None, alias
    # Two tables one noun could spell: no claim to check.
    assert entity_table_for_alias("invoice_count", ["invoice", "invoices"]) is None


def test_quoted_identifiers_face_the_same_bounds():
    """Guard pass: a quoted statement used to slip every regex-read
    bound — the filtered count here, and by the same route the joined,
    entity and aggregate bounds."""
    quoted = sql_invocation(
        'SELECT COUNT(*) AS "n" FROM "invoices" WHERE "invoices"."status" = \'READY\'',
        [{"n": 5000}],
    )
    result = _verify(quoted, INVOICE_STATS)
    assert result.disposition == "refused"
    (finding,) = result.plausibility
    assert finding.check == "run_sql.filtered_count_bound"
