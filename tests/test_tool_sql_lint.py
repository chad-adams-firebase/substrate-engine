"""The fan-out lint (fix pass 3, 4b baseline MT2): a deterministic
post-generation check on generated SQL, in the alias guard's slot.
Every shape the baseline produced is pinned here — the one that
breached and the correct ones that must not draw a false repair."""

from engine.substrates.models import (
    DictionaryMap,
    DictionaryRow,
    DocProvenance,
    JoinPath,
    JoinStep,
)
from engine.tools.sql_lint import lint_fan_out, split_scopes
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
    column("invoice_history", "invoice_id", fk="invoices.id"),
    column("invoice_history", "actor"),
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
    ],
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
    assert "both columns are foreign keys to invoices.id" in reason
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
    assert "finding_feedback" not in reason.split("Join condition")[1].split("Count the")[0]


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

AVG_FANOUT = """
SELECT s.name AS supplier_name, AVG(l.extended_price) AS avg_line_price
FROM suppliers s
JOIN invoices i ON s.id = i.supplier_id
JOIN invoice_lines l ON i.id = l.invoice_id
GROUP BY s.name
"""

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
    assert "invoices.supplier_id is a foreign key" in reason
    assert "subquery joined back per entity" in reason
    assert "resend the statement unchanged" in reason


def test_avg_over_a_fanning_join_draws_the_lint():
    """W6's family: AVG joins the aggregate set — a fanned AVG weights
    repeated rows exactly like a fanned SUM."""
    reason = lint_fan_out(AVG_FANOUT, DICTIONARY, MAP)
    assert reason is not None
    assert "COUNT/SUM/AVG" in reason


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


def test_avg_coalesce_is_the_repair_shape_and_stays_silent():
    assert lint_fan_out(S2_REPAIRED_WITH_COALESCE, DICTIONARY, MAP) is None


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
