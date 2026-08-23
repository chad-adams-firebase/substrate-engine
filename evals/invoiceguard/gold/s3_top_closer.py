"""S3 gold (Story 3): reviews completed per auditor — transitions to
a terminal review status by a user whose role is auditor (system
actors are the documented trap)."""


def gold(world):
    rows = world.sql(
        "SELECT h.actor, COUNT(*) AS n FROM invoice_history h "
        "JOIN users u ON u.short_name = h.actor AND u.role = 'auditor' "
        "WHERE h.to_status IN ('CLOSED', 'NO_REVIEW_NEEDED') "
        "GROUP BY h.actor ORDER BY n DESC"
    )
    return {
        "name": rows[0]["actor"],
        "count": rows[0]["n"],
        "next_tier": rows[1]["n"],
        "roster": [row["actor"] for row in rows],
    }
