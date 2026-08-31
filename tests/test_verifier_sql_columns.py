"""Select-list resolution (checks/sql_columns.py): result columns
traced back to stats columns from the SQL itself — deterministic,
unlike the display layer's token-suffix guessing."""

from engine.verifier.checks.sql_columns import resolve_select_columns


def test_aggregates_and_passthroughs_resolve_with_table_aliases():
    sql = (
        "SELECT s.name AS supplier_name, "
        "SUM(i.invoice_total) AS total_invoice_amount, "
        "AVG(i.opportunity) AS avg_opportunity, "
        "received_at "
        "FROM invoices i JOIN suppliers s ON s.id = i.supplier_id"
    )
    resolved = resolve_select_columns(sql)
    assert resolved["supplier_name"].table == "suppliers"
    assert resolved["supplier_name"].column == "name"
    assert resolved["supplier_name"].aggregate is None
    assert resolved["total_invoice_amount"].table == "invoices"
    assert resolved["total_invoice_amount"].column == "invoice_total"
    assert resolved["total_invoice_amount"].aggregate == "sum"
    assert resolved["avg_opportunity"].aggregate == "avg"
    # Unqualified passthrough: no table hint — the check searches the
    # queried tables' stats.
    assert resolved["received_at"].table is None
    assert resolved["received_at"].column == "received_at"


def test_sum_coalesce_resolves_and_avg_coalesce_does_not():
    """COALESCE(col, 0) leaves a SUM unchanged (W1's shape); for AVG
    it changes the population, so the [min, max] bound is void."""
    resolved = resolve_select_columns(
        "SELECT SUM(COALESCE(i.opportunity, 0)) AS total_opportunity, "
        "AVG(COALESCE(i.opportunity, 0)) AS avg_opportunity "
        "FROM invoices i"
    )
    assert resolved["total_opportunity"].aggregate == "sum"
    assert resolved["total_opportunity"].column == "opportunity"
    assert "avg_opportunity" not in resolved


def test_complex_expressions_are_unresolvable_not_guessed():
    sql = (
        "SELECT SUM(CASE WHEN ff.valid_exception = 1 THEN 0 "
        "ELSE COALESCE(f.amount, 0) END) AS effective_savings, "
        "COUNT(DISTINCT f.id) AS finding_count, "
        "i.invoice_total * 1.1 AS padded_total "
        "FROM findings f JOIN invoices i ON f.invoice_id = i.id"
    )
    assert resolve_select_columns(sql) == {}


def test_unknown_qualifiers_and_keywords_do_not_resolve():
    resolved = resolve_select_columns(
        "SELECT x.mystery AS m, DISTINCT_THING FROM invoices i"
    )
    assert "m" not in resolved  # x was never introduced in FROM/JOIN
    assert "DISTINCT_THING" in resolved  # a real (if odd) bare column


def test_outer_scope_only_a_cte_reference_carries_its_cte_name():
    """The outer FROM names a CTE, not a real table: resolution
    surfaces the CTE name, which no stats table will match — the
    documented unchecked case."""
    sql = (
        "WITH daily AS (SELECT DATE(at) AS day, COUNT(*) AS n "
        "FROM invoice_history GROUP BY DATE(at)) "
        "SELECT AVG(d.n) AS avg_per_day FROM daily d"
    )
    resolved = resolve_select_columns(sql)
    assert resolved["avg_per_day"].table == "daily"
    assert resolved["avg_per_day"].aggregate == "avg"


def test_split_survives_functions_with_commas():
    resolved = resolve_select_columns(
        "SELECT SUM(COALESCE(a.x, 0)) AS total_x, a.y AS why "
        "FROM alpha a"
    )
    assert set(resolved) == {"total_x", "why"}
    assert resolved["why"].column == "y"
