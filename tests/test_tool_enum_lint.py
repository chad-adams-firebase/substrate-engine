"""The enum-literal lint (tools/enum_lint.py): a filter on a value the
column's dictionary enum never holds draws a challenge naming the
observed values — and the column that does hold the literal, when one
does. Play Session #2's R-A (to_status = 'REJECTED') is the fixture."""

from engine.substrates.models import DictionaryRow, Provenance
from engine.tools.enum_lint import lint_enum_literals

HUMAN = Provenance(source="human", confidence=1.0, needs_validation=False)
LIFECYCLE = ["RECEIVED", "READY", "CLAIMED", "IN_REVIEW", "CLOSED", "LAPSED", "NO_REVIEW_NEEDED"]


def _row(table, column, enum=None):
    return DictionaryRow(
        table_name=table, column_name=column, data_type="VARCHAR",
        enum_values=enum, enum_source="data_scan" if enum else None, provenance=HUMAN,
    )


DICTIONARY = [
    _row("invoice_history", ""),
    _row("invoice_history", "to_status", LIFECYCLE),
    _row("invoice_history", "from_status", LIFECYCLE[:5]),
    _row("invoice_history", "actor"),
    _row("invoice_history", "invoice_id"),
    _row("invoices", ""),
    _row("invoices", "id"),
    _row("invoices", "status", ["CLOSED", "LAPSED", "NO_REVIEW_NEEDED", "READY"]),
    _row("invoices", "supplier_acceptance", ["ACCEPTED", "REJECTED"]),
    _row("review_reports", "disposition", ["APPROVED", "CORRECTIONS_REQUESTED"]),
    _row("review_reports", "invoice_id"),
    _row("users", "short_name"),
    _row("users", "status", ["active", "inactive"]),
]

R_A = (
    "SELECT ih.actor AS reviewer, COUNT(DISTINCT ih.id) AS rejection_count "
    "FROM invoice_history ih JOIN users u ON ih.actor = u.short_name "
    "WHERE ih.to_status = 'REJECTED' GROUP BY ih.actor ORDER BY rejection_count DESC"
)


def test_the_play_sessions_rejected_status_is_challenged_and_redirected():
    reason = lint_enum_literals(R_A, DICTIONARY)
    assert reason is not None
    assert reason.startswith("Enum check: `invoice_history.to_status` never takes 'REJECTED'")
    assert "observed values: RECEIVED, READY, CLAIMED, IN_REVIEW, CLOSED, LAPSED, NO_REVIEW_NEEDED" in reason
    assert "'REJECTED' is an observed value of `invoices.supplier_acceptance`" in reason
    assert reason.endswith("resend the statement unchanged.")


def test_a_transient_lifecycle_value_names_the_column_that_holds_it():
    """invoices.status = 'IN_REVIEW' is legal but never observed on the
    resting-status column; the lint says where the value lives."""
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'IN_REVIEW'", DICTIONARY
    )
    assert "`invoices.status` never takes 'IN_REVIEW'" in reason
    assert "observed values: CLOSED, LAPSED, NO_REVIEW_NEEDED, READY" in reason
    # Both history columns hold it; the hint names every one.
    assert "'IN_REVIEW' is an observed value of `invoice_history.from_status`, `invoice_history.to_status`" in reason


def test_in_lists_challenge_only_the_missing_members():
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h "
        "WHERE h.to_status IN ('CLOSED', 'REJECTED', 'NO_REVIEW_NEEDED')",
        DICTIONARY,
    )
    assert reason.count("never takes") == 1
    assert "'REJECTED'" in reason and "'CLOSED'" not in reason.split("observed values")[0]


def test_observed_literals_and_enum_free_columns_are_silent():
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history WHERE to_status = 'CLOSED'", DICTIONARY
    ) is None
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history WHERE actor = 'finch'", DICTIONARY
    ) is None
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h WHERE h.to_status = 'CLOSED' "
        "AND h.actor = 'nobody'",
        DICTIONARY,
    ) is None


def test_inequalities_and_unresolvable_columns_are_silent():
    # <> on a nonexistent value is a no-op filter, not a wrong answer.
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history WHERE to_status <> 'REJECTED'", DICTIONARY
    ) is None
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history WHERE to_status != 'REJECTED'", DICTIONARY
    ) is None
    # A qualifier the FROM clause never introduced.
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h WHERE x.to_status = 'REJECTED'", DICTIONARY
    ) is None
    # An unqualified column two queried tables own: ambiguous, silent.
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoices i JOIN users u ON u.id = i.claimed_by "
        "WHERE status = 'GHOST'",
        DICTIONARY,
    ) is None
    # A CTE alias is not a dictionary table.
    assert lint_enum_literals(
        "WITH t AS (SELECT 'REJECTED' AS to_status) SELECT COUNT(*) AS n FROM t "
        "WHERE t.to_status = 'REJECTED'",
        DICTIONARY,
    ) is None


def test_literals_in_comments_and_escaped_quotes_are_handled():
    assert lint_enum_literals(
        "-- to_status = 'REJECTED' in a comment\n"
        "SELECT COUNT(*) AS n FROM invoice_history WHERE to_status = 'CLOSED'",
        DICTIONARY,
    ) is None
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM review_reports WHERE disposition = 'DON''T'", DICTIONARY
    )
    assert "never takes 'DON'T'" in reason


def test_a_dictionary_without_enums_means_no_lint():
    plain = [_row("invoices", "status")]
    assert lint_enum_literals("SELECT 1 AS x FROM invoices WHERE status = 'Z'", plain) is None
