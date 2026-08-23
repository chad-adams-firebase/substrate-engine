"""NP3 gold: the READY backlog and its recovery opportunity, rounded
to cents — float tails in money cells are their own observation."""


def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS n, SUM(opportunity) AS opportunity "
        "FROM invoices WHERE status = 'READY'"
    )
    return {"count": row["n"], "money": round(row["opportunity"], 2)}
