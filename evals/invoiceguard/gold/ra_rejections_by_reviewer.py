"""R-A gold (Play Session #2): audit rejections by reviewer. Default
reading: correction_ignored findings attributed to the auditor who
CLOSED the reviewed (prior) revision — neither findings nor reviews
name a reviewer. Second reading (declared on the metric): reviews with
CORRECTIONS_REQUESTED per that same closer. There is no REJECTED
status anywhere."""


def gold(world):
    ignored = world.sql(
        "SELECT x.reviewer, COUNT(*) AS n FROM ("
        "  SELECT (SELECT h.actor FROM invoice_history h "
        "          WHERE h.invoice_id = prior.id AND h.to_status = 'CLOSED') AS reviewer "
        "  FROM findings f "
        "  JOIN invoices i ON i.id = f.invoice_id "
        "  JOIN invoices prior ON prior.id = i.prior_revision_id "
        "  WHERE f.rule_name = 'correction_ignored') x "
        "GROUP BY x.reviewer ORDER BY n DESC, x.reviewer"
    )
    requested = world.sql(
        "SELECT h.actor AS reviewer, COUNT(*) AS n "
        "FROM review_reports rr "
        "JOIN invoice_history h ON h.invoice_id = rr.invoice_id "
        "AND h.to_status = 'CLOSED' "
        "WHERE rr.disposition = 'CORRECTIONS_REQUESTED' "
        "GROUP BY h.actor ORDER BY n DESC, h.actor"
    )
    return {
        "top": ignored[0]["reviewer"],
        "top_count": ignored[0]["n"],
        "reviewers": len(ignored),
        "total": sum(row["n"] for row in ignored),
        "by_reviewer": {row["reviewer"]: row["n"] for row in ignored},
        "requested_top_count": requested[0]["n"],
    }
