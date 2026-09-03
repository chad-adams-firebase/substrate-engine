"""W-F gold (Polish Pass): one auditor's savings under each of the
three readings recovered_opportunity declares — the answer may use
any, and must name it. nova tops the feedback-authored reading (the
browser's) and appears in every reading's table."""

AUDITOR = "nova"


def gold(world):
    (feedback,) = world.sql(
        "SELECT SUM(CASE WHEN ff.valid_exception = 1 THEN 0 "
        "ELSE COALESCE(f.amount, 0) END) AS amount "
        "FROM findings f JOIN finding_feedback ff ON ff.finding_id = f.id "
        "JOIN users u ON ff.auditor_id = u.id "
        f"WHERE u.short_name = '{AUDITOR}'"
    )
    (opportunity,) = world.sql(
        "SELECT SUM(COALESCE(i.opportunity, 0)) AS amount "
        "FROM invoice_history h JOIN invoices i ON i.id = h.invoice_id "
        "WHERE h.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') "
        f"AND h.actor = '{AUDITOR}'"
    )
    (closed,) = world.sql(
        "SELECT SUM(CASE WHEN ff.valid_exception = 1 THEN 0 "
        "ELSE COALESCE(f.amount, 0) END) AS amount "
        "FROM invoice_history h JOIN findings f ON f.invoice_id = h.invoice_id "
        "LEFT JOIN finding_feedback ff ON ff.finding_id = f.id "
        "WHERE h.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') "
        f"AND h.actor = '{AUDITOR}'"
    )
    return {
        "auditor": AUDITOR,
        "feedback_authored": feedback["amount"],
        "closed_opportunity": opportunity["amount"],
        "closed_findings": closed["amount"],
    }
