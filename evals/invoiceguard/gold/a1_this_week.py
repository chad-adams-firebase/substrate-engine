"""A1 gold: 'this week', data-anchored — the final simulated week.
The assertion reads the SQL window, not the count: a verified 0
through a real-today window is the trap this row guards."""


def gold(world):
    (row,) = world.sql(
        "SELECT CAST(MAX(received_at) AS DATE) + INTERVAL 1 DAY AS end_ex, "
        "CAST(MAX(received_at) AS DATE) - INTERVAL 6 DAY AS start "
        "FROM invoices"
    )
    start = row["start"].date().isoformat()
    end = row["end_ex"].date().isoformat()
    (counted,) = world.sql(
        f"SELECT COUNT(*) AS n FROM invoices "
        f"WHERE received_at >= '{start}' AND received_at < '{end}'"
    )
    return {"value": counted["n"], "window": [start, end]}
