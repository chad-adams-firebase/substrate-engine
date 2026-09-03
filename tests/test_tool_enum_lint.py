"""The enum-literal lint (tools/enum_lint.py): a column filtered only
on values its dictionary enum never holds draws a challenge naming the
observed values and keeping the query on its table. Play Session #2's
R-A (to_status = 'REJECTED') is the fixture that fires; the
post-duration bank's AMB2 (a mixed IN list over invoices.status) is the
fixture that must stay silent — the guard pass."""

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

# The post-duration AMB2 rep 1, attempt 1, verbatim: the four
# non-terminal lifecycle states over the resting-status column, of
# which the data shows only READY. It returns the correct 78.
AMB2_ATTEMPT_1 = (
    "SELECT \n    COUNT(*) AS invoice_count\nFROM \n    invoices\nWHERE \n"
    "    status IN ('RECEIVED', 'READY', 'CLAIMED', 'IN_REVIEW')"
)


def test_the_play_sessions_rejected_status_is_challenged_on_its_own_table():
    reason = lint_enum_literals(R_A, DICTIONARY)
    assert reason is not None
    assert reason.startswith("Enum check: `invoice_history.to_status` never takes 'REJECTED'")
    assert "observed values: RECEIVED, READY, CLAIMED, IN_REVIEW, CLOSED, LAPSED, NO_REVIEW_NEEDED" in reason
    assert "Keep the query on `invoice_history`" in reason
    assert reason.endswith("resend the statement unchanged.")


def test_the_challenge_never_names_where_else_the_literal_lives():
    """The guard pass's principle: a challenge names what is wrong with
    the query, never a different subject table. 'REJECTED' is an
    observed value of invoices.supplier_acceptance; the coverage pass
    said so in the challenge, and AMB2's rep 1 read the same sentence
    about invoice_history as an instruction to query it."""
    reason = lint_enum_literals(R_A, DICTIONARY)
    assert "supplier_acceptance" not in reason
    assert "invoices" not in reason.replace("invoice_history", "")
    assert "if that is the column meant" not in reason
    assert "is an observed value of" not in reason


def test_a_mixed_in_list_is_silent_because_the_query_can_be_right():
    """AMB2's attempt 1: READY is observed, so the statement returns the
    READY count and the three never-observed members are no-ops — the
    same no-op the <> exemption already recognises. A repair round on
    a correct query is exactly the mechanism that breached."""
    assert lint_enum_literals(AMB2_ATTEMPT_1, DICTIONARY) is None
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h "
        "WHERE h.to_status IN ('CLOSED', 'REJECTED', 'NO_REVIEW_NEEDED')",
        DICTIONARY,
    ) is None
    # The same disjunction spelled with OR reads the same way.
    assert lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'READY' OR status = 'RECEIVED'",
        DICTIONARY,
    ) is None


def test_an_in_list_with_no_observed_member_is_challenged_once_naming_them_all():
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h "
        "WHERE h.to_status IN ('REJECTED', 'GHOST')",
        DICTIONARY,
    )
    assert reason.count("never takes") == 1
    assert "never takes 'REJECTED', 'GHOST' in this data" in reason
    assert "Keep the query on `invoice_history`" in reason


def test_a_lone_transient_lifecycle_value_is_challenged_without_a_pointer():
    """invoices.status = 'IN_REVIEW' alone is guaranteed empty; the
    challenge names the observed values and keeps the query on
    invoices — it no longer says the value lives in invoice_history
    (the coverage-pass ruling, reversed by the guard pass)."""
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'IN_REVIEW'", DICTIONARY
    )
    assert "`invoices.status` never takes 'IN_REVIEW'" in reason
    assert "observed values: CLOSED, LAPSED, NO_REVIEW_NEEDED, READY" in reason
    assert "Keep the query on `invoices`" in reason
    assert "invoice_history" not in reason


def test_two_empty_columns_draw_two_sentences_and_one_trailer():
    reason = lint_enum_literals(
        "SELECT COUNT(*) AS n FROM invoice_history h JOIN invoices i ON i.id = h.invoice_id "
        "WHERE h.to_status = 'REJECTED' AND i.status = 'IN_REVIEW'",
        DICTIONARY,
    )
    assert reason.count("Enum check:") == 2
    assert reason.count("resend the statement unchanged") == 1


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


def test_quoted_identifiers_are_read_like_bare_ones():
    """Guard pass: the quoted form of R-A used to pass the lint entirely."""
    quoted = R_A.replace("FROM invoice_history ih", 'FROM "invoice_history" AS "ih"').replace(
        "ih.to_status", '"ih"."to_status"'
    )
    assert '"ih"."to_status"' in quoted
    assert lint_enum_literals(quoted, DICTIONARY) == lint_enum_literals(R_A, DICTIONARY)
