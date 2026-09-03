"""The display resolver's view of the select list (tools/sql_select.py):
every item as an expression tree, CTEs and derived tables followed to
the real column behind them, arithmetic kept, CASE and subqueries
declared Opaque. Play Session #2's statements are the fixtures — the
$1,641.64-beside-2202.2 row and the 4698.219326550668 ratio."""

from engine.tools.sql_select import (
    Aggregate,
    Arith,
    Column,
    Number,
    Numeric,
    Opaque,
    resolve_select_columns,
    resolve_select_items,
    source_column,
)

# Turn 2.12: original_cost and corrected_cost live in a CTE.
TURN_2_12 = """WITH rule_savings AS (
    SELECT f.rule_name,
           SUM(CASE WHEN ff.valid_exception = 1 THEN 0 ELSE COALESCE(f.amount, 0) END) AS effective_savings
    FROM findings f LEFT JOIN finding_feedback ff ON ff.finding_id = f.id
    GROUP BY f.rule_name ORDER BY effective_savings DESC LIMIT 1
), example_invoice AS (
    SELECT f.invoice_id, f.line_number, f.amount AS flagged_amount,
           l.extended_price AS original_cost,
           (l.extended_price - f.amount) AS corrected_cost
    FROM findings f
    LEFT JOIN finding_feedback ff ON ff.finding_id = f.id
    JOIN rule_savings rs ON rs.rule_name = f.rule_name
    JOIN invoice_lines l ON f.invoice_id = l.invoice_id AND f.line_number = l.line_number
    LIMIT 1
)
SELECT i.invoice_number, s.name AS supplier_name, l.description AS line_item_description,
       ei.original_cost, ei.flagged_amount, ei.corrected_cost
FROM example_invoice ei
JOIN invoices i ON ei.invoice_id = i.id
JOIN suppliers s ON i.supplier_id = s.id
JOIN invoice_lines l ON ei.invoice_id = l.invoice_id AND ei.line_number = l.line_number"""

# Turn 2.11: a ratio of two CTE columns.
TURN_2_11 = """WITH rule_savings AS (
    SELECT f.rule_name,
           SUM(CASE WHEN ff.valid_exception = 1 THEN 0 ELSE COALESCE(f.amount, 0) END) AS effective_savings
    FROM findings f LEFT JOIN finding_feedback ff ON ff.finding_id = f.id GROUP BY f.rule_name
), top_rule AS (
    SELECT rule_name, effective_savings FROM rule_savings ORDER BY effective_savings DESC LIMIT 1
), invoice_counts AS (
    SELECT f.rule_name, COUNT(DISTINCT f.invoice_id) AS invoice_count,
           SUM(CASE WHEN ff.valid_exception = 1 THEN 0 ELSE COALESCE(f.amount, 0) END) AS total_savings
    FROM findings f LEFT JOIN finding_feedback ff ON ff.finding_id = f.id GROUP BY f.rule_name
)
SELECT tr.rule_name, ic.invoice_count,
       ic.total_savings * 1.0 / ic.invoice_count AS avg_savings_per_invoice
FROM top_rule tr JOIN invoice_counts ic ON tr.rule_name = ic.rule_name"""


def test_cte_columns_resolve_to_the_real_columns_behind_them():
    items = resolve_select_items(TURN_2_12)
    assert items["original_cost"] == Column("invoice_lines", "extended_price")
    assert items["flagged_amount"] == Column("findings", "amount")
    assert items["corrected_cost"] == Arith(
        "-", Column("invoice_lines", "extended_price"), Column("findings", "amount")
    )
    assert items["supplier_name"] == Column("suppliers", "name")
    assert items["invoice_number"] == Column("invoices", "invoice_number")


def test_arithmetic_over_cte_columns_keeps_its_shape_and_its_opaque_parts():
    items = resolve_select_items(TURN_2_11)
    assert items["invoice_count"] == Aggregate("count", Column("findings", "invoice_id"), True)
    ratio = items["avg_savings_per_invoice"]
    assert isinstance(ratio, Arith) and ratio.op == "/"
    assert ratio.left == Arith("*", Opaque(), Number())  # SUM(CASE ...) is Opaque
    assert ratio.right == Aggregate("count", Column("findings", "invoice_id"), True)


def test_derived_tables_are_followed_and_scalar_subqueries_are_opaque():
    items = resolve_select_items(
        "SELECT x.reviewer, x.cost, COUNT(*) AS n FROM ("
        "  SELECT (SELECT h.actor FROM invoice_history h WHERE h.invoice_id = f.invoice_id) AS reviewer,"
        "         l.extended_price AS cost"
        "  FROM findings f JOIN invoice_lines l ON l.invoice_id = f.invoice_id"
        ") x GROUP BY x.reviewer, x.cost"
    )
    assert items["reviewer"] == Opaque()
    assert items["cost"] == Column("invoice_lines", "extended_price")
    assert items["n"] == Aggregate("count", None)


def test_plain_aggregates_arithmetic_and_wrappers():
    items = resolve_select_items(
        "SELECT SUM(i.invoice_total) AS total, AVG(i.opportunity) AS avg_opp, "
        "MIN(l.unit_rate) AS lowest, received_at, "
        "SUM(f.amount) * 1.0 / COUNT(DISTINCT i.id) AS avg_per_invoice, "
        "SUM(f.amount) / SUM(i.invoice_total) AS flagged_share, "
        "ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM invoices), 2) AS flag_pct, "
        "COALESCE(SUM(i.opportunity), 0) AS total_opportunity, "
        "CASE WHEN i.rush_flag = 1 THEN 1 ELSE 0 END AS rushed, "
        "i.invoice_total * 1.1 AS padded "
        "FROM invoices i JOIN findings f ON f.invoice_id = i.id "
        "JOIN invoice_lines l ON l.invoice_id = i.id"
    )
    assert items["total"] == Aggregate("sum", Column("invoices", "invoice_total"))
    assert items["avg_opp"] == Aggregate("avg", Column("invoices", "opportunity"))
    assert items["lowest"] == Aggregate("min", Column("invoice_lines", "unit_rate"))
    assert items["received_at"] == Column(None, "received_at")
    assert items["avg_per_invoice"] == Arith(
        "/",
        Arith("*", Aggregate("sum", Column("findings", "amount")), Number()),
        Aggregate("count", Column("invoices", "id"), True),
    )
    assert items["flagged_share"] == Arith(
        "/",
        Aggregate("sum", Column("findings", "amount")),
        Aggregate("sum", Column("invoices", "invoice_total")),
    )
    assert items["flag_pct"] == Opaque()  # a scalar subquery inside
    assert items["total_opportunity"] == Aggregate("sum", Column("invoices", "opportunity"))
    assert items["rushed"] == Opaque()
    assert items["padded"] == Arith("*", Column("invoices", "invoice_total"), Number())


def test_string_literals_and_comments_never_confuse_the_scan():
    items = resolve_select_items(
        "-- the FROM in this comment is not a FROM\n"
        "SELECT s.name AS supplier_name, COUNT(*) AS n "
        "FROM suppliers s WHERE s.code = 'FROM (SELECT' GROUP BY s.name"
    )
    assert items == {
        "supplier_name": Column("suppliers", "name"),
        "n": Aggregate("count", None),
    }


def test_unknown_qualifiers_and_unaliased_expressions_are_skipped():
    items = resolve_select_items(
        "SELECT x.mystery AS m, SUM(i.opportunity), i.opportunity FROM invoices i"
    )
    assert items["m"] == Opaque()
    assert "SUM(i.opportunity)" not in items
    assert items["opportunity"] == Column("invoices", "opportunity")


def test_source_column_is_the_single_leaf_or_nothing():
    assert source_column(Aggregate("avg", Column("invoices", "opportunity"))) == Column(
        "invoices", "opportunity"
    )
    assert source_column(Column(None, "x")) == Column(None, "x")
    assert source_column(Arith("-", Column("a", "x"), Column("b", "y"))) is None
    assert source_column(Aggregate("count", None)) is None
    assert source_column(Opaque()) is None


def test_the_verifier_view_still_sees_the_outer_scope_only():
    """The two views share the parse but not the contract: the Verifier
    keeps its unchecked-CTE case (a stats table named 'example_invoice'
    does not exist), the display follows the CTE to invoice_lines."""
    verifier = resolve_select_columns(TURN_2_12)
    assert verifier["original_cost"].table == "example_invoice"
    assert resolve_select_items(TURN_2_12)["original_cost"].table == "invoice_lines"


def test_two_argument_age_is_the_subtraction_it_is():
    """Duration pass: AGE(a, b) is a - b, an INTERVAL over two
    timestamps, so the interval lint sees it; one-argument AGE is
    against the wall clock and stays Opaque."""
    items = resolve_select_items(
        "SELECT AGE(lv.at, en.at) AS gap, AGE(lv.at) AS since "
        "FROM invoice_history en JOIN invoice_history lv ON lv.invoice_id = en.invoice_id"
    )
    assert items["gap"] == Arith(
        "-", Column("invoice_history", "at"), Column("invoice_history", "at")
    )
    assert items["since"] == Opaque()


def test_an_opaque_item_keeps_its_source_text_outside_equality():
    """The Verifier's degenerate-duration warn reads what the parse
    still declines — a CASE-wrapped duration — lexically; the text
    rides on the Opaque and never affects equality, so `== Opaque()`
    still means 'the parse declined'."""
    items = resolve_select_items(
        "SELECT AVG(CASE WHEN i.scored_at > i.received_at THEN EPOCH(i.scored_at - i.received_at) END) / 3600.0 AS avg_hours FROM invoices i"
    )
    assert items["avg_hours"] == Opaque()
    assert items["avg_hours"].text.startswith("AVG(CASE WHEN")


def test_numeric_functions_keep_the_aggregate_above_them_visible():
    """Guard pass: EPOCH/DATE_DIFF/JULIAN are numbers to the parse, so
    the recommended EPOCH-first shape is no longer the one shape the
    guards cannot read — AVG(EPOCH(a - b)) / 3600 is an AVG over a
    number, SUM(DATE_DIFF(...)) a SUM, JULIAN(a) - JULIAN(b) a
    difference of numbers, and an aggregate inside the call is seen."""
    items = resolve_select_items(
        "SELECT AVG(EPOCH(lv.at - en.at)) / 3600.0 AS avg_hours, "
        "SUM(DATE_DIFF('hour', en.at, lv.at)) AS total_hours, "
        "JULIAN(lv.at) - JULIAN(en.at) AS days, "
        "EPOCH(MAX(lv.at) - MIN(en.at)) AS span, "
        "DATE_PART('epoch', lv.at) AS stamp "
        "FROM invoice_history en JOIN invoice_history lv ON lv.invoice_id = en.invoice_id"
    )
    at = Column("invoice_history", "at")
    assert items["avg_hours"] == Arith(
        "/", Aggregate("avg", Numeric("epoch", (Arith("-", at, at),))), Number()
    )
    assert items["total_hours"] == Aggregate("sum", Numeric("date_diff", (Opaque(), at, at)))
    assert items["days"] == Arith("-", Numeric("julian", (at,)), Numeric("julian", (at,)))
    assert items["span"] == Numeric(
        "epoch", (Arith("-", Aggregate("max", at), Aggregate("min", at)),)
    )
    assert items["stamp"] == Numeric("date_part", (Opaque(), at))
    # A number of units inherits no column's format: the alias decides.
    assert source_column(items["avg_hours"]) is None


def test_extract_stays_outside_the_parse():
    """EXTRACT(EPOCH FROM ...) carries a FROM the select-list scan reads
    as the statement's own; the item never resolves. Recorded in the
    guard pass as a residual, not built — this pins the known shape."""
    items = resolve_select_items(
        "SELECT AVG(EXTRACT(EPOCH FROM (i.scored_at - i.received_at))) / 60 AS minutes "
        "FROM invoices i"
    )
    assert "minutes" not in items


def test_quoted_identifiers_resolve_like_bare_ones():
    """Guard pass: a quoted statement used to bypass the whole parse
    (and every lint and bound built on it)."""
    bare = resolve_select_items(
        "SELECT SUM(i.invoice_total) AS total, i.status, COUNT(*) AS n "
        "FROM invoices i WHERE i.status = 'READY'"
    )
    quoted = resolve_select_items(
        'SELECT SUM("i"."invoice_total") AS "total", "i"."status", COUNT(*) AS "n" '
        'FROM "invoices" AS "i" WHERE "i"."status" = \'READY\''
    )
    assert quoted == bare
    assert bare["total"] == Aggregate("sum", Column("invoices", "invoice_total"))


def test_count_distinct_is_recorded_on_the_aggregate():
    """The Verifier's count checks read the flag: a distinct count
    compares against distinct_count, a plain one against row_count."""
    items = resolve_select_items(
        "SELECT COUNT(DISTINCT f.invoice_id) AS n, COUNT(*) AS rows_seen "
        "FROM findings f"
    )
    assert items["n"] == Aggregate("count", Column("findings", "invoice_id"), True)
    assert items["rows_seen"] == Aggregate("count", None)
    assert items["rows_seen"].distinct is False
