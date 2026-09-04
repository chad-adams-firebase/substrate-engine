"""The SQL text layer (tools/sql_scopes.py): one walk over a statement
that every lint and parse reads. Pinned here: the scope split is the
walk's texts in post-order; a CTE body and a derived table are named
scopes that see what was declared before them and never themselves;
each scope keeps the literals it blanked, in order, so a lint can read
what a predicate compared without searching the statement for it."""

from engine.tools.sql_scopes import (
    from_table_of,
    literals_between,
    scope_tree,
    split_alias,
    split_scopes,
    table_references,
)

# S2 reps 2/4's hidden fan (post-Block-4): three CTEs, the third reading
# the first, the statement reading the last two.
FOUR_SCOPES = """
WITH flagged_lines AS (
  SELECT l.id AS line_id FROM invoice_lines l
  LEFT JOIN findings f ON l.invoice_id = f.invoice_id AND f.rule_name = 'service_hours_excessive'
  WHERE l.item_code = 'SVC-4410'
),
total_lines AS (SELECT COUNT(*) AS total_count FROM invoice_lines WHERE item_code = 'SVC-4410'),
flagged_count AS (SELECT COUNT(*) AS flagged_count FROM flagged_lines)
SELECT flagged_count.flagged_count * 1.0 / total_lines.total_count AS item_flag_rate
FROM flagged_count, total_lines
"""

# W-F attempt 2: the CTE body carries the terminal-status filter.
W_F_BODY = """
WITH auditor_savings AS (
  SELECT ih.actor AS auditor, SUM(COALESCE(i.opportunity, 0)) AS realized_savings
  FROM invoice_history ih JOIN invoices i ON i.id = ih.invoice_id
  WHERE ih.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') GROUP BY ih.actor
)
SELECT u.short_name AS auditor, a.realized_savings
FROM auditor_savings a JOIN users u ON u.short_name = a.auditor
WHERE u.role = 'auditor' ORDER BY a.realized_savings DESC
"""


def test_the_scope_split_is_the_tree_in_post_order():
    tree = scope_tree(FOUR_SCOPES)
    assert [scope.text for scope in tree] == split_scopes(FOUR_SCOPES)
    assert [scope.kind for scope in tree] == ["cte", "cte", "cte", "statement"]
    assert tree[-1].kind == "statement" and tree[-1].name is None
    assert "(__subquery__)" in tree[-1].text and "COUNT(*)" in tree[2].text


def test_ctes_and_derived_tables_are_named_and_see_only_what_came_before():
    tree = scope_tree(FOUR_SCOPES)
    flagged_lines, total_lines, flagged_count, statement = tree
    assert (flagged_lines.name, total_lines.name, flagged_count.name) == (
        "flagged_lines", "total_lines", "flagged_count",
    )
    assert flagged_lines.named == {}
    assert sorted(total_lines.named) == ["flagged_lines"]
    assert sorted(flagged_count.named) == ["flagged_lines", "total_lines"]
    assert flagged_count.named["flagged_lines"] is flagged_lines
    assert sorted(statement.named) == ["flagged_count", "flagged_lines", "total_lines"]

    # A derived table in a CTE body is that body's, not the statement's;
    # the statement's own derived tables are its, with or without AS.
    nested = scope_tree(
        "WITH c AS (SELECT x.k FROM (SELECT k FROM t) x) "
        "SELECT * FROM (SELECT k FROM c) AS d JOIN (SELECT k FROM t) e ON d.k = e.k"
    )
    kinds = {(scope.kind, scope.name) for scope in nested}
    assert kinds == {
        ("derived", "x"), ("cte", "c"), ("derived", "d"), ("derived", "e"),
        ("statement", None),
    }
    statement = nested[-1]
    assert sorted(statement.named) == ["c", "d", "e"]
    body = next(scope for scope in nested if scope.name == "c")
    assert sorted(body.named) == ["x"]
    # A recursive CTE does not see itself; a scalar or EXISTS subquery
    # is a subquery, never a derived table, whatever follows it.
    recursive = scope_tree("WITH RECURSIVE t AS (SELECT 1 AS n) SELECT n FROM t")
    assert recursive[0].name == "t" and recursive[0].named == {}
    scalar = scope_tree(
        "SELECT a, (SELECT COUNT(*) FROM t) AS n FROM s "
        "WHERE EXISTS (SELECT 1 FROM u WHERE u.k = s.k)"
    )
    assert [scope.kind for scope in scalar] == ["subquery", "subquery", "statement"]
    assert scalar[-1].named == {}


def test_each_scope_keeps_its_own_literals_in_order():
    body, statement = scope_tree(W_F_BODY)
    assert body.literals == ("CLOSED", "NO_REVIEW_NEEDED")
    assert statement.literals == ("auditor",)
    assert "IN ('', '')" in body.text
    start = body.text.index("IN (")
    end = body.text.index(")", start) + 1
    assert literals_between(body, start, end) == ["CLOSED", "NO_REVIEW_NEEDED"]
    assert literals_between(body, 0, start) == []

    # A literal with parentheses and an escaped quote neither opens a
    # scope nor loses its quote; a subquery's literal is the subquery's.
    (only,) = scope_tree("SELECT * FROM t WHERE note = '(x''y)'")
    assert only.literals == ("(x'y)",) and only.text.endswith("= ''")
    inner, outer = scope_tree(
        "SELECT * FROM t WHERE k IN (SELECT k FROM u WHERE s = 'a') AND s = 'b'"
    )
    assert inner.literals == ("a",) and outer.literals == ("b",)


def test_table_references_keep_order_and_aliases():
    scope = "SELECT COUNT(*) FROM invoices i LEFT JOIN reviewed ri ON i.id = ri.invoice_id JOIN users ON users.id = i.claimer_id"
    assert table_references(scope) == [
        ("invoices", "i"), ("reviewed", "ri"), ("users", None),
    ]
    assert from_table_of(scope) == "invoices"
    assert from_table_of("SELECT 1") is None


def test_the_select_item_grammar_names_a_result_column():
    assert split_alias("SUM(i.total) AS total") == ("total", "SUM(i.total)")
    assert split_alias("ih.actor") == ("actor", "ih.actor")
    assert split_alias("actor") == ("actor", "actor")
    assert split_alias("COUNT(*)") == (None, "COUNT(*)")
    assert split_alias("DISTINCT") == (None, "DISTINCT")
