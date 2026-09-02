"""W-B gold (Play Session #2): how often suppliers apply the
corrections we request. Population: correction lines whose reviewed
invoice has a successor; applied: the successor's same-numbered line
bills requested_rate exactly or is gone (a removal is applied when the
line is gone). Both correction kinds count — rate-only is 34/48 and
not the headline. The per-review reading (invoices.supplier_acceptance
on the prior) is the declared second interpretation."""

APPLIED = (
    "NOT EXISTS (SELECT 1 FROM invoice_lines sl "
    "WHERE sl.invoice_id = succ.id AND sl.line_number = rrl.line_number "
    "AND (rrl.remove_requested = 1 OR sl.unit_rate <> rrl.requested_rate))"
)


def gold(world):
    (lines,) = world.sql(
        "SELECT COUNT(*) AS population, "
        f"SUM(CASE WHEN {APPLIED} THEN 1 ELSE 0 END) AS applied "
        "FROM review_report_lines rrl "
        "JOIN review_reports rr ON rr.id = rrl.review_report_id "
        "JOIN invoices inv ON inv.id = rr.invoice_id "
        "JOIN invoices succ ON succ.prior_revision_id = inv.id"
    )
    (reviews,) = world.sql(
        "SELECT COUNT(*) AS with_resubmission, "
        "SUM(CASE WHEN inv.supplier_acceptance = 'ACCEPTED' THEN 1 ELSE 0 END) "
        "AS accepted "
        "FROM review_reports rr JOIN invoices inv ON inv.id = rr.invoice_id "
        "WHERE inv.supplier_acceptance IS NOT NULL"
    )
    return {
        "population": lines["population"],
        "applied": lines["applied"],
        "rate": round(lines["applied"] / lines["population"], 4),
        "reviews_with_resubmission": reviews["with_resubmission"],
        "reviews_accepted": reviews["accepted"],
        "review_rate": round(reviews["accepted"] / reviews["with_resubmission"], 4),
    }
