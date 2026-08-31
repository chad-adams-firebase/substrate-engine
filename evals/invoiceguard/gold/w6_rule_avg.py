"""W6 gold (play pass): average finding amount per rule — the
finding-level grain. The play answer averaged invoices.opportunity
across a join instead, 1.5–25× inflated, and shipped verified with
the attribution unstated."""


def gold(world):
    rows = world.sql(
        "SELECT rule_name, AVG(amount) AS avg_amount, COUNT(*) AS n "
        "FROM findings WHERE amount IS NOT NULL "
        "GROUP BY rule_name ORDER BY avg_amount DESC"
    )
    top = rows[0]
    return {
        "rule": top["rule_name"],
        "avg_amount": top["avg_amount"],
        "finding_count": top["n"],
    }
