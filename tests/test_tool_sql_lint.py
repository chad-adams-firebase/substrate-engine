"""The fan-out lint (fix pass 3, 4b baseline MT2): a deterministic
post-generation check on generated SQL, in the alias guard's slot.
Every shape the baseline produced is pinned here — the one that
breached and the correct ones that must not draw a false repair."""

from engine.substrates.models import (
    CardinalityCondition,
    DictionaryMap,
    DictionaryRow,
    DocProvenance,
    JoinPath,
    JoinStep,
)
from engine.tools.sql_lint import lint_fan_out
from engine.tools.sql_scopes import split_scopes, table_aliases
from tests.verifier_support import MACHINE


def column(table: str, name: str, fk: str | None = None, pk=False) -> DictionaryRow:
    return DictionaryRow(
        table_name=table, column_name=name, data_type="BIGINT",
        is_primary_key=pk, fk_target=fk, provenance=MACHINE,
    )


DICTIONARY = [
    column("invoices", "id", pk=True),
    column("invoices", "supplier_id", fk="suppliers.id"),
    column("invoice_lines", "invoice_id", fk="invoices.id"),
    column("suppliers", "id", pk=True),
    column("findings", "id", pk=True),
    column("findings", "invoice_id", fk="invoices.id"),
    column("compliance_reports", "id", pk=True),
    column("compliance_reports", "invoice_id", fk="invoices.id"),
    column("compliance_rules", "id", pk=True),
    column("compliance_rules", "compliance_report_id", fk="compliance_reports.id"),
    column("finding_feedback", "id", pk=True),
    column("finding_feedback", "finding_id", fk="findings.id"),
    column("invoice_history", "id", pk=True),
    column("invoice_history", "invoice_id", fk="invoices.id"),
    column("invoice_history", "actor"),
    column("invoice_history", "to_status"),
    column("invoice_history", "from_status"),
    column("users", "short_name"),
]

MAP = DictionaryMap(
    provenance=DocProvenance(
        source="machine", confidence=0.5, needs_validation=True
    ),
    join_paths=[
        JoinPath(
            name="findings_to_feedback",
            steps=[JoinStep(from_table="findings", from_column="id",
                            to_table="finding_feedback", to_column="finding_id")],
            cardinality="one_to_one",
        ),
        JoinPath(
            name="invoices_to_compliance",
            steps=[
                JoinStep(from_table="invoices", from_column="id",
                         to_table="compliance_reports", to_column="invoice_id"),
                JoinStep(from_table="compliance_reports", from_column="id",
                         to_table="compliance_rules",
                         to_column="compliance_report_id"),
            ],
        ),
        # The pack's declaration (Close Pass): one history row per
        # invoice under a terminal status, and for the received
        # transition — executed against the world by --check-gold.
        JoinPath(
            name="invoices_to_history",
            steps=[JoinStep(from_table="invoices", from_column="id",
                            to_table="invoice_history", to_column="invoice_id")],
            one_to_one_when=[
                CardinalityCondition(
                    column="invoice_history.to_status",
                    values=["CLOSED", "NO_REVIEW_NEEDED"],
                ),
                CardinalityCondition(column="invoice_history.to_status", values=["RECEIVED"]),
                CardinalityCondition(column="invoice_history.from_status", values=["RECEIVED"]),
            ],
        ),
    ],
)

# The same map without the received-once conditions: what the history
# self-join draws when nothing vouches for its two sides.
MAP_WITHOUT_RECEIVED = MAP.model_copy(
    update={
        "join_paths": [
            path.model_copy(update={"one_to_one_when": path.one_to_one_when[:1]})
            if path.name == "invoices_to_history"
            else path
            for path in MAP.join_paths
        ]
    }
)

MT2_FANOUT = """
SELECT COUNT(*) AS critical_compliance_findings
FROM findings
JOIN compliance_reports ON findings.invoice_id = compliance_reports.invoice_id
JOIN compliance_rules ON compliance_reports.id = compliance_rules.compliance_report_id
WHERE compliance_rules.severity = 'CRITICAL'
"""

C5_LOOKUPS = """
SELECT s.code AS supplier_code, COUNT(*) AS rate_variance_count
FROM findings f
JOIN invoices i ON f.invoice_id = i.id
JOIN suppliers s ON i.supplier_id = s.id
WHERE f.rule_name = 'rate_variance'
GROUP BY s.code ORDER BY rate_variance_count DESC LIMIT 1
"""

S4_CORRELATED = """
SELECT COUNT(*) AS unmatched_invoices
FROM invoices i
WHERE i.adjustment_flag = 1
  AND ABS(i.invoice_total - (SELECT SUM(l.extended_price) FROM invoice_lines l WHERE l.invoice_id = i.id)) > 0.01
  AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.invoice_id = i.id AND f.rule_name = 'total_mismatch')
"""

NP4_DISTINCT = """
SELECT COUNT(DISTINCT f.invoice_id) * 1.0 / (SELECT COUNT(*) FROM invoices) AS flag_rate
FROM findings f JOIN invoices ON f.invoice_id = invoices.id
"""

U5_ONE_TO_ONE = """
SELECT f.rule_name, SUM(CASE WHEN ff.valid_exception = 1 THEN 0 ELSE COALESCE(f.amount, 0) END) AS effective
FROM findings f LEFT JOIN finding_feedback ff ON ff.finding_id = f.id
GROUP BY f.rule_name ORDER BY effective DESC
"""

FO_EXCEPT_NAIVE = """
SELECT COUNT(*) AS invoices_with_exceptions
FROM invoices i
JOIN findings f ON f.invoice_id = i.id
JOIN finding_feedback ff ON ff.finding_id = f.id
WHERE ff.valid_exception = 1
"""

S3_NO_FK = """
SELECT users.short_name AS auditor,
       SUM(CASE WHEN invoice_history.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') THEN 1 ELSE 0 END) AS closed_reviews
FROM invoice_history JOIN users ON users.short_name = invoice_history.actor
WHERE users.role = 'auditor' GROUP BY users.short_name
"""


def test_scopes_split_on_subqueries_and_keep_function_parens():
    scopes = split_scopes(NP4_DISTINCT)
    assert len(scopes) == 2
    outer = scopes[-1]
    assert "COUNT(DISTINCT f.invoice_id)" in outer  # function parens inline
    assert "(__subquery__)" in outer
    assert "SELECT COUNT(*) FROM invoices" in scopes[0]


def test_mt2_fan_out_draws_the_lint_naming_the_join_path():
    reason = lint_fan_out(MT2_FANOUT, DICTIONARY, MAP)
    assert reason is not None
    assert "findings.invoice_id = compliance_reports.invoice_id" in reason
    assert "both columns are foreign keys with the same target" in reason
    assert "invoices.id" not in reason  # the unqueried target is not named
    assert "invoices_to_compliance" in reason
    assert "COUNT(DISTINCT <table>.id)" in reason
    assert "resend the statement unchanged" in reason
    # The second hop is a lookup from compliance_rules? No — from the
    # from-side compliance_reports to compliance_rules' FK: one-to-many.
    assert "compliance_reports.id = compliance_rules.compliance_report_id" in reason


def test_fk_lookup_chains_are_exempt():
    """C5/MT3: findings -> invoices -> suppliers are many-to-one
    lookups from the from-side — COUNT(*) keeps the findings grain."""
    assert lint_fan_out(C5_LOOKUPS, DICTIONARY, MAP) is None


def test_correlated_and_scalar_subqueries_are_not_joins():
    assert lint_fan_out(S4_CORRELATED, DICTIONARY, MAP) is None


def test_count_distinct_is_exempt():
    assert lint_fan_out(NP4_DISTINCT, DICTIONARY, MAP) is None


def test_declared_one_to_one_path_is_exempt():
    """U5's canonical shape: the map vouches that finding_feedback is
    at most one row per finding."""
    assert lint_fan_out(U5_ONE_TO_ONE, DICTIONARY, MAP) is None


def test_join_to_the_many_side_draws_the_lint():
    """FO-EXCEPT's naive shape: invoices -> findings multiplies the
    invoice grain (739 vs 536 distinct invoices in the world)."""
    reason = lint_fan_out(FO_EXCEPT_NAIVE, DICTIONARY, MAP)
    assert reason is not None
    assert "findings.invoice_id is a foreign key" in reason
    assert "COUNT(*) reads invoices" in reason
    assert "finding_feedback" not in reason.split("Aggregate each side")[0]


def test_join_without_a_foreign_key_is_challenged_not_exempt():
    """S3's users.short_name = invoice_history.actor: nothing in the
    dictionary vouches for it, so it draws one round — the model is
    licensed to resend unchanged."""
    reason = lint_fan_out(S3_NO_FK, DICTIONARY, MAP)
    assert reason is not None
    assert "no foreign key relates these columns" in reason


def test_no_aggregate_means_no_lint():
    listing = "SELECT i.id, f.rule_name FROM invoices i JOIN findings f ON f.invoice_id = i.id"
    assert lint_fan_out(listing, DICTIONARY, MAP) is None


# --- Play-pass extensions: SUM/AVG repairs, the DISTINCT band-aid ----

W1_OVERRIDE = """
SELECT s.name AS supplier_name,
       COUNT(DISTINCT i.id) AS invoice_count,
       SUM(i.invoice_total) AS total_invoice_amount,
       SUM(l.extended_price) AS total_line_amount
FROM suppliers s
JOIN invoices i ON s.id = i.supplier_id
JOIN invoice_lines l ON i.id = l.invoice_id
GROUP BY s.name
"""

# Polish Pass: the fixture reads the one side (invoices, repeated once
# per line) — its earlier AVG(l.extended_price) read the many side,
# which cannot fan, and is now the silent LINE_AVG below.
AVG_FANOUT = """
SELECT s.name AS supplier_name, AVG(i.invoice_total) AS avg_invoice_total
FROM suppliers s
JOIN invoices i ON s.id = i.supplier_id
JOIN invoice_lines l ON i.id = l.invoice_id
GROUP BY s.name
"""

LINE_AVG = AVG_FANOUT.replace(
    "AVG(i.invoice_total) AS avg_invoice_total",
    "AVG(l.extended_price) AS avg_line_price",
)

W7_BANDAID = """
SELECT u.short_name AS reviewer,
       SUM(DISTINCT COALESCE(i.opportunity, 0)) AS total_opportunity
FROM invoice_history ih
JOIN users u ON u.short_name = ih.actor
JOIN invoices i ON i.id = ih.invoice_id
GROUP BY u.short_name
"""


def test_w1_shape_still_trips_after_the_count_repair():
    """The play pass's W1 override: COUNT fixed to DISTINCT, SUMs left
    fanned. The SUMs alone keep the lint tripping — this is what the
    detection-only re-lint records on the executed attempt."""
    reason = lint_fan_out(W1_OVERRIDE, DICTIONARY, MAP)
    assert reason is not None
    # Direction-aware (Polish Pass): the step that repeats invoices is
    # the lines join, and the suppliers join — which repeats only
    # suppliers, a table no aggregate reads — is not named.
    assert "SUM(i.invoice_total) reads invoices" in reason
    assert "invoice_lines.invoice_id is a foreign key" in reason
    assert "invoices.supplier_id" not in reason
    assert "SUM(l.extended_price)" not in reason  # the many side cannot fan
    assert "Aggregate each side in its own scope" in reason
    assert "resend the statement unchanged" in reason


def test_avg_over_a_fanning_join_draws_the_lint():
    """W6's family: AVG joins the aggregate set — a fanned AVG weights
    repeated rows exactly like a fanned SUM."""
    reason = lint_fan_out(AVG_FANOUT, DICTIONARY, MAP)
    assert reason is not None
    assert "AVG(i.invoice_total) reads invoices" in reason
    assert lint_fan_out(LINE_AVG, DICTIONARY, MAP) is None


def test_sum_distinct_bandaid_is_challenged_not_exempt():
    """W7: SUM(DISTINCT ...) silently drops repeated values; it is a
    challenged pattern in a fanning scope, never a sanctioned repair.
    COUNT(DISTINCT ...) stays exempt (NP4 above)."""
    reason = lint_fan_out(W7_BANDAID, DICTIONARY, MAP)
    assert reason is not None
    assert "silently drops repeated values" in reason


def test_sum_distinct_without_a_risky_join_stays_silent():
    single = "SELECT SUM(DISTINCT opportunity) AS s FROM invoices"
    assert lint_fan_out(single, DICTIONARY, MAP) is None


# --- Pin-pass extensions: the three post-play-pass breach shapes ------

MT2_EXPRESSION_JOIN = """
SELECT COUNT(*) AS critical_compliance_findings
FROM findings f
JOIN compliance_rules cr ON f.rule_name = CONCAT('compliance_', cr.rule_code)
WHERE cr.severity = 'CRITICAL'
"""

CONCAT_OPERATOR_JOIN = """
SELECT COUNT(*) AS n
FROM findings f
JOIN compliance_rules cr ON f.rule_name = 'compliance_' || cr.rule_code
"""

COMPOSITE_FK_VOUCHED = """
SELECT COUNT(*) AS n
FROM invoice_lines l
JOIN invoices i ON l.invoice_id = i.id
  AND DATE_TRUNC('month', l.created_at) = DATE_TRUNC('month', i.received_at)
"""

B5_DEAD_LEFT_JOIN = """
SELECT COUNT(DISTINCT invoices.id) AS invoices_with_findings
FROM invoices
LEFT JOIN findings ON findings.invoice_id = invoices.id
WHERE invoices.received_at >= '2026-05-23 00:00:00'
  AND invoices.received_at < '2026-05-30 00:00:00'
"""

LEFT_JOIN_ENRICHMENT = """
SELECT COUNT(DISTINCT i.id) AS n
FROM invoices i
LEFT JOIN findings f ON f.invoice_id = i.id
WHERE f.rule_name IS NOT NULL
"""

INNER_SEMI_JOIN = """
SELECT COUNT(DISTINCT i.id) AS n
FROM invoices i
JOIN findings f ON f.invoice_id = i.id
"""

DEAD_BUT_ONE_TO_ONE = """
SELECT COUNT(DISTINCT f.id) AS n
FROM findings f
LEFT JOIN finding_feedback ff ON ff.finding_id = f.id
"""

S2_AVG_OVER_NULL_SIDE = """
SELECT
    AVG(flagged_lines.is_flagged) AS item_flag_rate
FROM
    invoice_lines l
LEFT JOIN (
    SELECT f.invoice_id, f.line_number, 1.0 AS is_flagged
    FROM findings f
    WHERE f.rule_name = 'service_hours_excessive'
) flagged_lines
ON l.invoice_id = flagged_lines.invoice_id
   AND l.line_number = flagged_lines.line_number
WHERE l.item_code = 'SVC-4410'
"""

S2_REPAIRED_WITH_COALESCE = S2_AVG_OVER_NULL_SIDE.replace(
    "AVG(flagged_lines.is_flagged)", "AVG(COALESCE(flagged_lines.is_flagged, 0))"
)


def test_expression_join_is_risky_regardless_of_key_knowledge():
    """The post-play-pass MT2 breach: rule_name = CONCAT('compliance_',
    rule_code) is invisible to the equality parser, and the derived
    key is non-unique (10 codes, 4,216 rows) — 107,509 vs 254."""
    reason = lint_fan_out(MT2_EXPRESSION_JOIN, DICTIONARY, MAP)
    assert reason is not None
    assert "derives its key with an expression" in reason
    assert "compliance_rules" in reason
    assert "invoices_to_compliance" in reason  # the canonical path hint
    assert "resend the statement unchanged" in reason


def test_concatenation_operator_join_is_risky_too():
    reason = lint_fan_out(CONCAT_OPERATOR_JOIN, DICTIONARY, MAP)
    assert reason is not None
    assert "derives its key with an expression" in reason


def test_expression_beside_a_vouched_fk_equality_is_exempt():
    """AND-ed predicates only filter further: a plain FK-vouched
    equality in the same condition settles the join's grain, so the
    date expression draws nothing."""
    assert lint_fan_out(COMPOSITE_FK_VOUCHED, DICTIONARY, MAP) is None


def test_dead_left_join_draws_the_join_shape_challenge():
    """The post-play-pass B5 breach: LEFT JOIN findings referenced only
    in its own ON answers 'how many received', not 'how many had
    findings' — 161 vs 146, and COUNT(DISTINCT) slips the fan gate."""
    reason = lint_fan_out(B5_DEAD_LEFT_JOIN, DICTIONARY, MAP)
    assert reason is not None
    assert "referenced only inside its own ON condition" in reason
    assert "inner join" in reason
    assert "Fan-out check" not in reason  # only the join-shape paragraph
    assert "resend the statement unchanged" in reason


def test_left_join_whose_columns_are_used_is_not_dead():
    assert lint_fan_out(LEFT_JOIN_ENRICHMENT, DICTIONARY, MAP) is None


def test_inner_semi_join_filter_stays_silent():
    assert lint_fan_out(INNER_SEMI_JOIN, DICTIONARY, MAP) is None


def test_dead_left_join_declared_one_to_one_is_exempt():
    assert lint_fan_out(DEAD_BUT_ONE_TO_ONE, DICTIONARY, MAP) is None


def test_avg_over_the_null_side_of_a_left_join_is_challenged():
    """The post-play-pass S2 breach: AVG skips NULLs, so the unmatched
    lines vanish from the denominator and the rate saturates to 1.0.
    The subquery join is visible to this check (and only this one)."""
    reason = lint_fan_out(S2_AVG_OVER_NULL_SIDE, DICTIONARY, MAP)
    assert reason is not None
    assert "AVG skips NULLs" in reason
    assert "AVG(flagged_lines.is_flagged)" in reason
    assert "AVG(COALESCE(<column>, 0))" in reason
    assert "resend the statement unchanged" in reason


def test_avg_coalesce_is_the_repair_shape_for_the_null_check():
    """COALESCE repairs the NULL semantics, so that paragraph is gone.
    The derived join itself is visible since the Close Pass and reads
    through to the line-grain join to findings — the same composite
    key the flat S2 indicator draws the fan-out check on, deliberately
    undeclared (a line may carry two findings by schema)."""
    reason = lint_fan_out(S2_REPAIRED_WITH_COALESCE, DICTIONARY, MAP)
    assert reason is not None
    assert "AVG skips NULLs" not in reason
    assert reason.startswith("Fan-out check:")
    assert "flagged_lines.invoice_id reads findings.invoice_id" in reason


def test_avg_over_a_plain_left_joined_table_is_challenged():
    sql = """
    SELECT AVG(ih.duration) AS avg_duration
    FROM invoices i
    LEFT JOIN invoice_history ih ON ih.invoice_id = i.id
    """
    reason = lint_fan_out(sql, DICTIONARY, MAP)
    assert reason is not None
    assert "AVG(ih.duration)" in reason
    assert "nullable side" in reason


def test_expression_join_challenge_quotes_the_models_own_literal():
    """Block 2 rider: the challenge shows the SQL the model wrote, not
    _clean's literal-stripped copy — CONCAT('compliance_', …), never
    CONCAT('', …). The rebuilt fragment survives both spellings and
    the whitespace the model chose."""
    from engine.tools.sql_scopes import original_fragment

    reason = lint_fan_out(MT2_EXPRESSION_JOIN, DICTIONARY, MAP)
    assert "CONCAT('compliance_', cr.rule_code)" in reason
    assert "CONCAT('', cr.rule_code)" not in reason
    reason = lint_fan_out(CONCAT_OPERATOR_JOIN, DICTIONARY, MAP)
    assert "'compliance_' || cr.rule_code" in reason

    sql = "SELECT 1 FROM a JOIN b ON a.k = LOWER(  'X''y' ) -- note\n"
    assert original_fragment(sql, "a.k = LOWER( '' )") == "a.k = LOWER( 'X''y' )"
    # No match (the fragment never existed): the cleaned text, collapsed.
    assert original_fragment(sql, "a.k  =  ''") == "a.k = ''"


# --- Duration pass: S2's recommended indicator shape ------------------

S2_EXISTS_AVG = (
    "SELECT AVG(CASE WHEN EXISTS (SELECT 1 FROM findings f "
    "WHERE f.invoice_id = l.invoice_id AND f.line_number = l.line_number "
    "AND f.rule_name = 'service_hours_excessive') THEN 1.0 ELSE 0.0 END) "
    "AS item_flag_rate FROM invoice_lines l WHERE l.item_code = 'SVC-4410'"
)
S2_EXISTS_SUM = (
    "SELECT SUM(CASE WHEN EXISTS (SELECT 1 FROM findings f "
    "WHERE f.invoice_id = l.invoice_id AND f.line_number = l.line_number "
    "AND f.rule_name = 'service_hours_excessive') THEN 1 ELSE 0 END) * 1.0 "
    "/ COUNT(*) AS item_flag_rate FROM invoice_lines l WHERE l.item_code = 'SVC-4410'"
)
S2_LEFT_JOIN_INDICATOR = (
    "SELECT AVG(CASE WHEN f.id IS NOT NULL THEN 1.0 ELSE 0.0 END) AS item_flag_rate "
    "FROM invoice_lines l LEFT JOIN findings f ON l.invoice_id = f.invoice_id "
    "AND l.line_number = f.line_number AND f.rule_name = 'service_hours_excessive' "
    "WHERE l.item_code = 'SVC-4410'"
)


def test_the_exists_indicator_never_meets_the_lint_and_the_left_join_one_does():
    """Post-coverage S2: the gotcha's recommended LEFT JOIN indicator drew
    the fan-out challenge on two reps (both invoice_id columns are FKs
    to invoices; the composite join is not declared one-to-one). The
    EXISTS indicator hoists to a subquery the outer scope never joins,
    so a recommended shape never meets a lint."""
    assert lint_fan_out(S2_EXISTS_AVG, DICTIONARY, MAP) is None
    assert lint_fan_out(S2_EXISTS_SUM, DICTIONARY, MAP) is None
    reason = lint_fan_out(S2_LEFT_JOIN_INDICATOR, DICTIONARY, MAP)
    assert reason is not None and reason.startswith("Fan-out check:")


MT2_FANOUT_QUOTED = """
SELECT COUNT(*) AS "critical_compliance_findings"
FROM "findings"
JOIN "compliance_reports" ON "findings"."invoice_id" = "compliance_reports"."invoice_id"
JOIN "compliance_rules" ON "compliance_reports"."id" = "compliance_rules"."compliance_report_id"
WHERE "compliance_rules"."severity" = 'CRITICAL'
"""


def test_quoted_identifiers_trip_the_lint_like_bare_ones():
    """Guard pass: every lint reads identifiers with a bare-name regex,
    so a quoted statement bypassed all of them; now it reads the same."""
    assert lint_fan_out(MT2_FANOUT_QUOTED, DICTIONARY, MAP) == lint_fan_out(
        MT2_FANOUT, DICTIONARY, MAP
    )


def test_an_unaliased_table_before_join_still_registers():
    """FROM findings JOIN compliance_reports ON …: the alias scan read
    JOIN as findings' alias and never saw compliance_reports (a latent
    hole the guard pass's principle test surfaced; no live statement
    hit it, the pinned model aliases every table)."""
    (scope,) = split_scopes(MT2_FANOUT)
    assert table_aliases(scope) == {
        "findings": "findings",
        "compliance_reports": "compliance_reports",
        "compliance_rules": "compliance_rules",
    }
    assert table_aliases("FROM invoices JOIN findings ON findings.invoice_id = invoices.id") == {
        "invoices": "invoices",
        "findings": "findings",
    }
    # An honest alias, with or without AS, still registers.
    assert table_aliases("FROM invoices AS i JOIN findings f ON f.invoice_id = i.id") == {
        "invoices": "invoices", "i": "invoices", "findings": "findings", "f": "findings",
    }


# --- Polish Pass: the direction rule ----------------------------------

# The flagship table's two live attempts (browser, 2026-09-03).
FLAGSHIP_ATTEMPT_1 = """
SELECT s.name AS supplier_name,
       SUM(i.invoice_total) AS total_invoice_amount,
       SUM(il.extended_price) AS total_line_item_amount
FROM suppliers s
LEFT JOIN invoices i ON s.id = i.supplier_id
LEFT JOIN invoice_lines il ON i.id = il.invoice_id
GROUP BY s.name ORDER BY s.name
"""
FLAGSHIP_ATTEMPT_2 = """
SELECT s.name AS supplier_name,
       (SELECT SUM(i.invoice_total) FROM invoices i WHERE i.supplier_id = s.id) AS total_invoice_amount,
       (SELECT SUM(il.extended_price) FROM invoices i JOIN invoice_lines il ON i.id = il.invoice_id
        WHERE i.supplier_id = s.id) AS total_line_item_amount
FROM suppliers s ORDER BY s.name
"""
# W1's first turn: every aggregate reads the many side.
W1_TURN_0 = """
SELECT s.name AS supplier_name, COUNT(i.id) AS invoice_count,
       SUM(i.invoice_total) AS total_invoice_amount, SUM(i.opportunity) AS total_opportunity
FROM suppliers s LEFT JOIN invoices i ON s.id = i.supplier_id
GROUP BY s.name ORDER BY invoice_count DESC
"""
# W1 reps 3/4's second turn: the line count pre-aggregated per invoice
# in a derived table, joined back — invisible to the step scan, and
# the outer aggregates read invoices, which only suppliers' join
# touches (and suppliers is what it repeats).
W1_DERIVED_LINES = """
SELECT s.name AS supplier_name, COUNT(DISTINCT i.id) AS invoice_count,
       SUM(i.invoice_total) AS total_invoice_amount, SUM(il.line_count) AS total_invoice_line_count
FROM suppliers s
LEFT JOIN invoices i ON s.id = i.supplier_id
LEFT JOIN (SELECT invoice_id, COUNT(*) AS line_count FROM invoice_lines GROUP BY invoice_id) il
  ON i.id = il.invoice_id
GROUP BY s.name ORDER BY s.name
"""
TWO_MANY_SIDES = """
SELECT i.id AS invoice_id, SUM(l.extended_price) AS line_total, SUM(f.amount) AS finding_total
FROM invoices i
JOIN invoice_lines l ON l.invoice_id = i.id
JOIN findings f ON f.invoice_id = i.id
GROUP BY i.id
"""
LOOKUP_SIDE_SUM = """
SELECT s.name AS supplier_name, SUM(s.credit_limit) AS credit
FROM invoices i JOIN suppliers s ON i.supplier_id = s.id
GROUP BY s.name
"""


def test_the_correlated_flagship_shape_is_silent_and_the_flat_one_is_not():
    """The brief's two live statements: attempt 2 aggregates each side
    in its own scope (the lines scope joins invoices ⟵ invoice_lines and
    reads the many side); attempt 1 reads invoices across the lines join
    that repeats it. The lint challenged both before, and the correct
    resend shipped [UNVERIFIED] via fan_out_override."""
    assert lint_fan_out(FLAGSHIP_ATTEMPT_2, DICTIONARY, MAP) is None
    reason = lint_fan_out(FLAGSHIP_ATTEMPT_1, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(i.invoice_total) reads invoices" in reason
    assert "invoices.id = invoice_lines.invoice_id" in reason
    assert "SUM(il.extended_price)" not in reason
    assert "suppliers.id = invoices.supplier_id" not in reason


def test_aggregating_the_many_side_per_one_side_cannot_fan():
    """W1's first turn — five reps, four runs, all challenged on
    suppliers.id = invoices.supplier_id, which repeats suppliers only."""
    assert lint_fan_out(W1_TURN_0, DICTIONARY, MAP) is None
    assert lint_fan_out(W1_DERIVED_LINES, DICTIONARY, MAP) is None


def test_two_many_sides_of_one_table_repeat_each_other():
    reason = lint_fan_out(TWO_MANY_SIDES, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(l.extended_price) reads invoice_lines" in reason
    assert "SUM(f.amount) reads findings" in reason
    assert "joins invoices to both" in reason


def test_aggregating_the_lookup_side_is_a_fan_the_old_rule_missed():
    """A many-to-one lookup repeats the one side once per from-side
    row: SUM over the looked-up table fans. The from-side exemption
    used to exempt this by table position alone."""
    reason = lint_fan_out(LOOKUP_SIDE_SUM, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(s.credit_limit) reads suppliers" in reason
    assert "repeats each suppliers row once per invoices row" in reason


def test_an_indicator_over_a_correlated_subquery_counts_the_row_grain():
    """A CASE over EXISTS(...) reads no outer column: it is attributed
    to the FROM table like COUNT(*), so the lookup chain stays silent
    (the correction_application_rate template's shape)."""
    sql = """
    SELECT COUNT(*) AS n,
           SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM invoice_lines sl WHERE sl.invoice_id = i.id)
               THEN 1 ELSE 0 END) AS lineless
    FROM findings f JOIN invoices i ON f.invoice_id = i.id
    """
    assert lint_fan_out(sql, DICTIONARY, MAP) is None


# --- Close Pass: cardinality under a declared filter -------------------

# W-F attempt 2 / U-WHO reps 2-4 (post-Block-4): the sum inside the CTE
# body reads invoices across the history join, which fans in general
# and is one row per invoice under the terminal-status filter.
WF_CLOSED_SAVINGS = """
WITH auditor_savings AS (
  SELECT ih.actor AS auditor, SUM(COALESCE(i.opportunity, 0)) AS realized_savings
  FROM invoice_history ih JOIN invoices i ON i.id = ih.invoice_id
  WHERE ih.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') GROUP BY ih.actor
)
SELECT u.short_name AS auditor, a.realized_savings
FROM auditor_savings a JOIN users u ON u.short_name = a.auditor
WHERE u.role = 'auditor' ORDER BY a.realized_savings DESC
"""
WF_UNFILTERED = """
SELECT ih.actor AS auditor, SUM(COALESCE(i.opportunity, 0)) AS realized_savings
FROM invoice_history ih JOIN invoices i ON i.id = ih.invoice_id
GROUP BY ih.actor ORDER BY realized_savings DESC
"""
# AMB1 attempt 1: the filter stands in the LEFT JOIN's own ON.
AMB1_ATTEMPT_1_FILTER_IN_ON = """
SELECT DATE(i.received_at) AS received_date, COUNT(*) AS unreviewed_invoices
FROM invoices i
LEFT JOIN invoice_history ih ON i.id = ih.invoice_id AND ih.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED')
WHERE ih.id IS NULL
GROUP BY DATE(i.received_at) ORDER BY received_date
"""
HISTORY_FILTER_OUTSIDE_SET = WF_UNFILTERED.replace(
    "GROUP BY ih.actor", "WHERE ih.to_status = 'CLAIMED' GROUP BY ih.actor"
)
HISTORY_FILTER_NOT_IN = WF_UNFILTERED.replace(
    "GROUP BY ih.actor",
    "WHERE ih.to_status NOT IN ('CLOSED', 'NO_REVIEW_NEEDED') GROUP BY ih.actor",
)
HISTORY_FILTER_WITH_TOP_LEVEL_OR = WF_UNFILTERED.replace(
    "GROUP BY ih.actor",
    "WHERE ih.to_status = 'CLOSED' OR ih.actor = 'nova' GROUP BY ih.actor",
)
HISTORY_FILTER_SUBSET = WF_UNFILTERED.replace(
    "GROUP BY ih.actor", "WHERE ih.to_status = 'CLOSED' GROUP BY ih.actor"
)
# Two history aliases, the filter on one: the other step still fans.
HISTORY_FILTER_ON_THE_OTHER_ALIAS = """
SELECT a.actor AS closer, SUM(COALESCE(i.opportunity, 0)) AS closed_opportunity
FROM invoices i
JOIN invoice_history a ON a.invoice_id = i.id
JOIN invoice_history b ON b.invoice_id = i.id
WHERE a.to_status = 'CLOSED'
GROUP BY a.actor
"""
# The body written without an alias: the bare column resolves through
# the dictionary when one in-scope table owns it (AMB1 rep 3's shape).
HISTORY_FILTER_UNQUALIFIED = """
SELECT actor AS auditor, SUM(COALESCE(i.opportunity, 0)) AS realized_savings
FROM invoice_history JOIN invoices i ON i.id = invoice_history.invoice_id
WHERE to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') GROUP BY actor
"""
# W3's flat self-join: two rows of one invoice's history, each vouched
# one-per-invoice by its own filter (received once, left RECEIVED once).
W3_SELF_JOIN = """
SELECT AVG(DATE_DIFF('second', r.at, rr.at)) / 3600.0 AS avg_hours_to_ready
FROM invoice_history r JOIN invoice_history rr ON r.invoice_id = rr.invoice_id
WHERE r.to_status = 'RECEIVED' AND rr.from_status = 'RECEIVED' AND rr.to_status = 'READY'
"""
AVG_OVER_A_TERMINAL_LEFT_JOIN = """
SELECT AVG(ih.duration) AS avg_duration
FROM invoices i
LEFT JOIN invoice_history ih ON ih.invoice_id = i.id AND ih.to_status = 'CLOSED'
"""


def test_a_declared_filter_makes_the_history_join_one_to_one():
    """W-F ×5 and U-WHO ×3 on the post-Block-4 report: the terminal
    status leaves one history row per invoice, so the sum over invoices
    cannot fan — a fact the pack declares and --check-gold executes."""
    assert lint_fan_out(WF_CLOSED_SAVINGS, DICTIONARY, MAP) is None
    assert lint_fan_out(AMB1_ATTEMPT_1_FILTER_IN_ON, DICTIONARY, MAP) is None
    assert lint_fan_out(HISTORY_FILTER_SUBSET, DICTIONARY, MAP) is None
    assert lint_fan_out(HISTORY_FILTER_UNQUALIFIED, DICTIONARY, MAP) is None
    reason = lint_fan_out(WF_UNFILTERED, DICTIONARY, MAP)
    assert reason is not None
    assert "invoice_history.invoice_id is a foreign key" in reason


def test_a_filter_the_declaration_does_not_cover_still_fans():
    for sql in (
        HISTORY_FILTER_OUTSIDE_SET,
        HISTORY_FILTER_NOT_IN,
        HISTORY_FILTER_WITH_TOP_LEVEL_OR,
        HISTORY_FILTER_ON_THE_OTHER_ALIAS,
    ):
        reason = lint_fan_out(sql, DICTIONARY, MAP)
        assert reason is not None, sql
        assert "reads invoices" in reason


def test_a_self_join_vouched_on_both_sides_is_one_to_one():
    """W3's shape: both columns are foreign keys with the same target,
    and each alias's filter leaves one row per invoice — with the
    received-once conditions declared the join is one-to-one; without
    them it is the shared-target fan it always was."""
    assert lint_fan_out(W3_SELF_JOIN, DICTIONARY, MAP) is None
    reason = lint_fan_out(W3_SELF_JOIN, DICTIONARY, MAP_WITHOUT_RECEIVED)
    assert reason is not None
    assert "both columns are foreign keys with the same target" in reason


def test_avg_over_a_conditionally_one_to_one_left_join_is_exempt():
    """The NULL-semantics check exempts a declared one_to_one join
    (U5's precedent); a join one-to-one under its own ON filter is the
    same shape."""
    assert lint_fan_out(AVG_OVER_A_TERMINAL_LEFT_JOIN, DICTIONARY, MAP) is None


# --- Close Pass: a scope's projection vouches for its key ---------------

# AMB1 attempt 2 (×5, post-Block-4): a DISTINCT projection of the join
# key cannot multiply; the challenge had said "no foreign key relates
# these columns" because a CTE name has no dictionary row.
AMB1_DISTINCT_CTE = """
WITH reviewed_invoices AS (
  SELECT DISTINCT ih.invoice_id FROM invoice_history ih
  WHERE ih.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED')
)
SELECT DATE(i.received_at) AS received_date, COUNT(*) AS unreviewed_invoices
FROM invoices i LEFT JOIN reviewed_invoices ri ON i.id = ri.invoice_id
WHERE ri.invoice_id IS NULL
GROUP BY DATE(i.received_at) ORDER BY received_date
"""
AMB1_DISTINCT_CTE_UNALIASED = AMB1_DISTINCT_CTE.replace(
    "SELECT DISTINCT ih.invoice_id FROM invoice_history ih\n  WHERE ih.to_status",
    "SELECT DISTINCT invoice_id FROM invoice_history\n  WHERE to_status",
)
GROUPED_CTE_ON_KEY_SUM = """
WITH invoice_amounts AS (
  SELECT i.supplier_id, SUM(i.invoice_total) AS total FROM invoices i GROUP BY i.supplier_id
)
SELECT s.name AS supplier_name, SUM(ia.total) AS total_invoice_amount
FROM suppliers s JOIN invoice_amounts ia ON ia.supplier_id = s.id
GROUP BY s.name
"""
# GROUP BY s.id, s.name (live ×11): the key is the primary key alone.
GROUPED_CTE_PK_AND_NAME = """
WITH per_supplier AS (
  SELECT s.id AS supplier_id, s.name AS supplier_name, COUNT(i.id) AS invoice_count
  FROM suppliers s LEFT JOIN invoices i ON i.supplier_id = s.id
  GROUP BY s.id, s.name
)
SELECT p.supplier_name, SUM(i.invoice_total) AS total
FROM per_supplier p JOIN invoices i ON i.supplier_id = p.supplier_id
GROUP BY p.supplier_name
"""
# The grouped scope joined on its key from the FK side: the scope is
# the one side, repeated once per invoice — reading it fans, reading
# the invoices does not.
GROUPED_CTE_TO_FK_SIDE = """
WITH invoice_amounts AS (
  SELECT i.supplier_id, SUM(i.invoice_total) AS total FROM invoices i GROUP BY i.supplier_id
)
SELECT SUM(ia.total) AS doubled_total, SUM(i.invoice_total) AS true_total
FROM invoices i LEFT JOIN invoice_amounts ia ON i.supplier_id = ia.supplier_id
"""
DERIVED_TABLE_TO_FK_SIDE = """
SELECT SUM(ia.total) AS doubled_total, SUM(i.invoice_total) AS true_total
FROM invoices i
LEFT JOIN (SELECT i2.supplier_id, SUM(i2.invoice_total) AS total FROM invoices i2 GROUP BY i2.supplier_id) ia
  ON i.supplier_id = ia.supplier_id
"""
# Grouped on two columns, joined on one: not unique on the join, so the
# primary-key side is the one side and reading it fans.
GROUPED_CTE_ON_NON_KEY = """
WITH per_item AS (
  SELECT i.supplier_id, l.item_code, SUM(l.extended_price) AS amount
  FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id
  GROUP BY i.supplier_id, l.item_code
)
SELECT s.name AS supplier_name, SUM(s.credit_limit) AS credit, SUM(p.amount) AS spend
FROM suppliers s JOIN per_item p ON p.supplier_id = s.id
GROUP BY s.name
"""
# A lookup written as a CTE: its columns read through to suppliers, so
# the foreign key from invoices decides — the lookup side fans, the
# invoices side does not (LOOKUP_SIDE_SUM in CTE clothing).
LOOKUP_CTE = """
WITH supplier_names AS (SELECT s.id, s.name FROM suppliers s)
SELECT sn.name AS supplier_name, COUNT(*) AS invoice_count, SUM(i.invoice_total) AS total
FROM invoices i JOIN supplier_names sn ON sn.id = i.supplier_id
GROUP BY sn.name
"""
LOOKUP_CTE_SUM_OVER_LOOKUP = """
WITH supplier_credit AS (SELECT s.id, s.credit_limit FROM suppliers s)
SELECT SUM(sc.credit_limit) AS credit
FROM invoices i JOIN supplier_credit sc ON sc.id = i.supplier_id
"""
# FO_EXCEPT_NAIVE in a CTE: the filtered pass-through reads through to
# findings.invoice_id, a foreign key, and the invoice grain multiplies.
FILTERED_PASSTHROUGH_CTE_COUNT = """
WITH open_findings AS (SELECT f.id, f.invoice_id FROM findings f WHERE f.status = 'OPEN')
SELECT COUNT(*) AS invoices_with_open_findings
FROM invoices i JOIN open_findings o ON o.invoice_id = i.id
"""
FILTERED_PASSTHROUGH_CTE_COUNT_DISTINCT = FILTERED_PASSTHROUGH_CTE_COUNT.replace(
    "COUNT(*)", "COUNT(DISTINCT i.id)"
)
TWO_GROUPED_CTES_ON_SHARED_KEY = """
WITH totals AS (SELECT i.supplier_id, SUM(i.invoice_total) AS total FROM invoices i GROUP BY i.supplier_id),
counts AS (SELECT i.supplier_id, COUNT(*) AS n FROM invoices i GROUP BY i.supplier_id)
SELECT t.supplier_id, SUM(t.total) AS total, SUM(c.n) AS n
FROM totals t JOIN counts c ON c.supplier_id = t.supplier_id
GROUP BY t.supplier_id
"""
CTE_JOINED_TWICE = """
WITH per_supplier AS (SELECT i.supplier_id, SUM(i.invoice_total) AS total FROM invoices i GROUP BY i.supplier_id)
SELECT SUM(a.total) AS a_total, SUM(b.total) AS b_total
FROM per_supplier a JOIN per_supplier b ON a.supplier_id = b.supplier_id
"""
# S2 reps 1/3 (post-Block-4): two pass-through CTEs on the composite
# line key — the line-grain join the map leaves undeclared, now named
# through the tables behind the CTEs.
S2_CTE_PAIR = """
WITH line_population AS (SELECT l.invoice_id, l.line_number FROM invoice_lines l WHERE l.item_code = 'SVC-4410'),
flagged_lines AS (SELECT f.invoice_id, f.line_number FROM findings f WHERE f.rule_name = 'service_hours_excessive')
SELECT AVG(CASE WHEN fl.invoice_id IS NOT NULL THEN 1.0 ELSE 0.0 END) AS item_flag_rate
FROM line_population lp LEFT JOIN flagged_lines fl
  ON lp.invoice_id = fl.invoice_id AND lp.line_number = fl.line_number
"""
# A primary key passed through a body whose joins repeat its table:
# the pass-through vouches for nothing.
PASSTHROUGH_PK_THROUGH_A_FANNED_BODY = """
WITH x AS (SELECT i.id, l.extended_price FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id)
SELECT COUNT(*) AS n FROM findings f JOIN x ON x.id = f.invoice_id
"""


def test_a_distinct_projection_of_the_join_key_cannot_multiply():
    """AMB1 ×5 on the post-Block-4 report: the anti-join to a DISTINCT
    CTE on its key is one-to-one, aliased or not."""
    assert lint_fan_out(AMB1_DISTINCT_CTE, DICTIONARY, MAP) is None
    assert lint_fan_out(AMB1_DISTINCT_CTE_UNALIASED, DICTIONARY, MAP) is None


def test_a_grouped_scope_joined_on_its_key_is_one_per_key():
    assert lint_fan_out(GROUPED_CTE_ON_KEY_SUM, DICTIONARY, MAP) is None
    assert lint_fan_out(GROUPED_CTE_PK_AND_NAME, DICTIONARY, MAP) is None
    assert lint_fan_out(TWO_GROUPED_CTES_ON_SHARED_KEY, DICTIONARY, MAP) is None
    assert lint_fan_out(CTE_JOINED_TWICE, DICTIONARY, MAP) is None
    assert lint_fan_out(W1_DERIVED_LINES, DICTIONARY, MAP) is None


def test_a_grouped_scope_is_the_one_side_from_the_foreign_key_side():
    for sql in (GROUPED_CTE_TO_FK_SIDE, DERIVED_TABLE_TO_FK_SIDE):
        reason = lint_fan_out(sql, DICTIONARY, MAP)
        assert reason is not None, sql
        assert "SUM(ia.total) reads ia" in reason or "SUM(ia.total) reads invoice_amounts" in reason
        assert "once per invoices row" in reason
        assert "SUM(i.invoice_total)" not in reason
    reason = lint_fan_out(GROUPED_CTE_ON_NON_KEY, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(s.credit_limit) reads suppliers" in reason
    assert "SUM(p.amount)" not in reason


def test_a_scope_column_reads_through_to_the_table_behind_it():
    """A pass-through CTE carries the foreign-key knowledge of the
    column it projects: the lookup and the filtered many side behave
    exactly as their flat twins."""
    assert lint_fan_out(LOOKUP_CTE, DICTIONARY, MAP) is None
    reason = lint_fan_out(LOOKUP_CTE_SUM_OVER_LOOKUP, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(sc.credit_limit) reads supplier_credit" in reason
    assert "supplier_credit.id reads suppliers.id" in reason
    reason = lint_fan_out(FILTERED_PASSTHROUGH_CTE_COUNT, DICTIONARY, MAP)
    assert reason is not None
    assert "COUNT(*) reads invoices" in reason
    assert "invoices is one row per id; open_findings.invoice_id reads findings.invoice_id" in reason
    assert "once per open_findings row" in reason
    assert lint_fan_out(FILTERED_PASSTHROUGH_CTE_COUNT_DISTINCT, DICTIONARY, MAP) is None


def test_the_line_grain_cte_pair_stays_challenged_through_its_tables():
    reason = lint_fan_out(S2_CTE_PAIR, DICTIONARY, MAP)
    assert reason is not None
    assert "line_population.invoice_id reads invoice_lines.invoice_id" in reason
    assert "flagged_lines.invoice_id reads findings.invoice_id" in reason
    assert "both foreign keys with the same target" in reason
    assert "no foreign key relates these columns" in reason  # line_number


def test_a_pass_through_key_from_a_fanned_body_vouches_for_nothing():
    reason = lint_fan_out(PASSTHROUGH_PK_THROUGH_A_FANNED_BODY, DICTIONARY, MAP)
    assert reason is not None
    assert "x.id reads invoices.id, but the joins inside x repeat invoices" in reason


def test_a_scope_reads_its_own_key():
    """The projection reader, on the shapes the pinned model writes."""
    from engine.tools.sql_lint import _context, _scope_grain
    from engine.tools.sql_scopes import scope_tree

    def grain(sql: str, name: str):
        tree = scope_tree(sql)
        scope = next(s for s in tree if s.name == name)
        return _scope_grain(scope, _context(sql, DICTIONARY, MAP), {})

    assert grain(AMB1_DISTINCT_CTE, "reviewed_invoices").unique_on == {"invoice_id"}
    assert grain(GROUPED_CTE_ON_KEY_SUM, "invoice_amounts").unique_on == {"supplier_id"}
    assert grain(GROUPED_CTE_PK_AND_NAME, "per_supplier").unique_on == {"supplier_id"}
    assert grain(GROUPED_CTE_ON_NON_KEY, "per_item").unique_on == {"supplier_id", "item_code"}
    assert grain(LOOKUP_CTE, "supplier_names").unique_on is None
    assert grain(LOOKUP_CTE, "supplier_names").passthrough == {
        "id": ("suppliers", "id"), "name": ("suppliers", "name"),
    }
    by_ordinal = "WITH c AS (SELECT i.supplier_id, COUNT(*) AS n FROM invoices i GROUP BY 1) SELECT * FROM c"
    assert grain(by_ordinal, "c").unique_on == {"supplier_id"}
    by_expression = (
        "WITH c AS (SELECT DATE(i.received_at) AS received_date, COUNT(*) AS n "
        "FROM invoices i GROUP BY DATE(i.received_at)) SELECT * FROM c"
    )
    assert grain(by_expression, "c").unique_on == {"received_date"}
    by_alias = "WITH c AS (SELECT ih.actor AS reviewer, COUNT(*) AS n FROM invoice_history ih GROUP BY reviewer) SELECT * FROM c"
    assert grain(by_alias, "c").unique_on == {"reviewer"}
    # COUNT(DISTINCT x) is not a scope-level DISTINCT; DISTINCT * names
    # no key; an unmatched group expression and a UNION vouch for none.
    count_distinct = "WITH c AS (SELECT COUNT(DISTINCT i.id) AS n FROM invoices i) SELECT * FROM c"
    assert grain(count_distinct, "c").unique_on is None
    assert grain("WITH c AS (SELECT DISTINCT * FROM invoices i) SELECT * FROM c", "c").unique_on is None
    unmatched = "WITH c AS (SELECT i.supplier_id, COUNT(*) AS n FROM invoices i GROUP BY i.supplier_id, i.status) SELECT * FROM c"
    assert grain(unmatched, "c").unique_on is None
    union = "WITH c AS (SELECT DISTINCT i.id FROM invoices i UNION SELECT f.id FROM findings f) SELECT * FROM c"
    assert grain(union, "c").unique_on is None


# --- Close Pass: an aggregate over a CTE reads what the CTE's rows are --

# S2 reps 2/4 (post-Block-4): the composite LEFT JOIN hidden in a CTE
# with no aggregate, counted outside — 100% instead of 95.5%, silent
# before, caught only by the saturated-rate warn.
S2_HIDDEN_FAN = """
WITH flagged_lines AS (
  SELECT l.id AS line_id FROM invoice_lines l
  LEFT JOIN findings f ON l.invoice_id = f.invoice_id AND l.line_number = f.line_number
    AND f.rule_name = 'service_hours_excessive'
  WHERE l.item_code = 'SVC-4410'
),
total_lines AS (SELECT COUNT(*) AS total_count FROM invoice_lines WHERE item_code = 'SVC-4410'),
flagged_count AS (SELECT COUNT(*) AS flagged_count FROM flagged_lines)
SELECT flagged_count.flagged_count * 1.0 / total_lines.total_count AS item_flag_rate
FROM flagged_count, total_lines
"""
# S2 rep 5: the gotcha's EXISTS indicator inside a CTE, averaged outside.
S2_EXISTS_CTE = """
WITH flagged_lines AS (
  SELECT l.id AS line_id,
         CASE WHEN EXISTS (SELECT 1 FROM findings f WHERE f.invoice_id = l.invoice_id
                           AND f.line_number = l.line_number AND f.rule_name = 'service_hours_excessive')
              THEN 1.0 ELSE 0.0 END AS is_flagged
  FROM invoice_lines l WHERE l.item_code = 'SVC-4410'
)
SELECT AVG(is_flagged) AS item_flag_rate FROM flagged_lines
"""
# The flagship fan hidden in a CTE: the invoices column fans, the lines
# column does not — read through the CTE's own projection.
HIDDEN_W1_SUM = """
WITH x AS (
  SELECT i.invoice_total, l.extended_price
  FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id
)
SELECT SUM(invoice_total) AS total_invoice_amount, SUM(extended_price) AS total_line_amount FROM x
"""
CTE_CHAIN = """
WITH base AS (SELECT i.id AS invoice_id, l.id AS line_id FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id),
passthrough AS (SELECT invoice_id FROM base)
SELECT COUNT(*) AS n FROM passthrough
"""
DEDUPLICATED_CTE_PROPAGATES_NOTHING = """
WITH matched AS (
  SELECT DISTINCT l.id FROM invoice_lines l
  LEFT JOIN findings f ON l.invoice_id = f.invoice_id AND l.line_number = f.line_number
)
SELECT COUNT(*) AS n FROM matched
"""
# W3 reps 1/4: the history self-join in a CTE, averaged outside.
W3_CTE_SELF_JOIN = """
WITH received_to_ready AS (
  SELECT r.invoice_id, DATE_DIFF('second', r.at, rr.at) AS time_in_seconds
  FROM invoice_history r JOIN invoice_history rr ON r.invoice_id = rr.invoice_id
  WHERE r.to_status = 'RECEIVED' AND rr.from_status = 'RECEIVED' AND rr.to_status = 'READY'
)
SELECT AVG(time_in_seconds) / 3600.0 AS avg_hours_to_ready FROM received_to_ready
"""


def test_a_fan_hidden_in_a_cte_is_read_through_its_rows():
    """The ledger's known gap, closed: the aggregate reads the CTE, the
    CTE's rows are the line grain across the composite join nothing
    vouches for."""
    reason = lint_fan_out(S2_HIDDEN_FAN, DICTIONARY, MAP)
    assert reason is not None
    assert "COUNT(*) reads flagged_lines, whose rows are invoice_lines across join condition(s) nothing vouches for" in reason
    assert "invoice_lines.invoice_id = findings.invoice_id" in reason
    assert lint_fan_out(S2_EXISTS_CTE, DICTIONARY, MAP) is None
    assert lint_fan_out(DEDUPLICATED_CTE_PROPAGATES_NOTHING, DICTIONARY, MAP) is None


def test_a_cte_column_reads_what_its_expression_read():
    reason = lint_fan_out(HIDDEN_W1_SUM, DICTIONARY, MAP)
    assert reason is not None
    assert "SUM(invoice_total) reads x, whose rows are invoices" in reason
    assert "SUM(extended_price)" not in reason
    reason = lint_fan_out(CTE_CHAIN, DICTIONARY, MAP)
    assert reason is not None
    assert "COUNT(*) reads passthrough, whose rows are invoices" in reason


def test_the_history_self_join_cte_is_silent_only_because_the_map_vouches():
    assert lint_fan_out(W3_CTE_SELF_JOIN, DICTIONARY, MAP) is None
    reason = lint_fan_out(W3_CTE_SELF_JOIN, DICTIONARY, MAP_WITHOUT_RECEIVED)
    assert reason is not None
    assert "AVG(time_in_seconds) reads received_to_ready, whose rows are invoice_history" in reason
    assert "both columns are foreign keys with the same target" in reason


def test_the_challenge_names_exists_when_a_left_join_is_in_play():
    """A recommended shape must be one every guard can read: the EXISTS
    indicator hoists to a scope nothing joins, so it is named exactly
    where a LEFT JOIN fired — the flat S2 indicator, the hidden fan —
    and not for MT2's inner joins."""
    for sql in (S2_LEFT_JOIN_INDICATOR, S2_HIDDEN_FAN):
        reason = lint_fan_out(sql, DICTIONARY, MAP)
        assert "COUNT(DISTINCT <table>.id), or test whether a row has a match with EXISTS rather than a LEFT JOIN." in reason
    reason = lint_fan_out(MT2_FANOUT, DICTIONARY, MAP)
    assert "COUNT(DISTINCT <table>.id)." in reason
    assert "EXISTS" not in reason
