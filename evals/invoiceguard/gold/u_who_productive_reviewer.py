"""U-WHO gold: "most productive reviewer" has two defensible readings
and the row declares both (Phase 5 gate verdict §3, §5, §7 item 5).

By count: reviews completed per auditor — transitions to a terminal
review status by a user whose role is auditor, exactly S3's measure
(gold/s3_top_closer.py), so the two rows cannot drift apart.

By closed opportunity: the recovery opportunity on the invoices each
auditor closed, summed under the same terminal filter. That filter
leaves one history row per invoice (the fan-out lint's declared
condition, executed by --check-gold through this script), so the sum
is per invoice, not per join combination. Rounded to the cent as the
other money golds are.

The two readings crown different people: nova by count, ava by
dollars. Either is the right answer until the reading is asked for."""


def gold(world):
    by_count = world.sql(
        "SELECT h.actor, COUNT(*) AS n FROM invoice_history h "
        "JOIN users u ON u.short_name = h.actor AND u.role = 'auditor' "
        "WHERE h.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') "
        "GROUP BY h.actor ORDER BY n DESC"
    )
    by_opportunity = world.sql(
        "SELECT h.actor, "
        "ROUND(SUM(COALESCE(i.opportunity, 0)), 2) AS closed_opportunity "
        "FROM invoice_history h "
        "JOIN users u ON u.short_name = h.actor AND u.role = 'auditor' "
        "JOIN invoices i ON i.id = h.invoice_id "
        "WHERE h.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') "
        "GROUP BY h.actor ORDER BY closed_opportunity DESC"
    )
    return {
        "name": by_count[0]["actor"],
        "count": by_count[0]["n"],
        "next_tier": by_count[1]["n"],
        "opportunity_name": by_opportunity[0]["actor"],
        "closed_opportunity": by_opportunity[0]["closed_opportunity"],
        "opportunity_next": by_opportunity[1]["closed_opportunity"],
        "roster": [row["actor"] for row in by_count],
    }
