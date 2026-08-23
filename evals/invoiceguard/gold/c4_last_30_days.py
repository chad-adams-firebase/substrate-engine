"""C4 gold: invoices in the data-anchored trailing 30 days — the
verified-zero trap's correct answer."""


def gold(world):
    (row,) = world.sql(
        "SELECT CAST(MAX(received_at) AS DATE) - INTERVAL 30 DAY AS start "
        "FROM invoices"
    )
    start = row["start"].date().isoformat()
    (counted,) = world.sql(
        f"SELECT COUNT(*) AS n FROM invoices WHERE received_at >= '{start}'"
    )
    return {"value": counted["n"], "window": [start]}
