"""W2 gold (play pass, ASSOC): one defensible per-auditor attribution
— seen = distinct findings on invoices the auditor personally
completed; actioned = feedback rows the auditor authored. The play
session's answer attributed every invoice's reports and feedback to
every auditor who ever touched the invoice (rows with actioned >
seen); no generic check can see that, hence the xfail."""


def gold(world):
    rows = world.sql(
        "SELECT u.short_name AS auditor, "
        "(SELECT COUNT(DISTINCT f.id) FROM invoice_history ih "
        " JOIN findings f ON f.invoice_id = ih.invoice_id "
        " WHERE ih.actor = u.short_name "
        " AND ih.to_status IN ('CLOSED','NO_REVIEW_NEEDED')) AS findings_seen, "
        "(SELECT COUNT(*) FROM finding_feedback ff "
        " WHERE ff.auditor_id = u.id) AS findings_actioned "
        "FROM users u WHERE u.role = 'auditor' "
        "ORDER BY findings_seen DESC"
    )
    top = rows[0]
    return {
        "auditor": top["auditor"],
        "findings_seen": top["findings_seen"],
        "findings_actioned": top["findings_actioned"],
    }
