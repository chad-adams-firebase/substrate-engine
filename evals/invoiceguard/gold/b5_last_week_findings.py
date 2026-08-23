"""B5 gold: invoices received in the data-anchored final week that
have at least one finding. The window derives from the data, never
the wall clock."""


def gold(world):
    (row,) = world.sql(
        "SELECT CAST(MAX(received_at) AS DATE) + INTERVAL 1 DAY AS end_ex "
        "FROM invoices"
    )
    end = row["end_ex"].date().isoformat()
    (row,) = world.sql(
        "SELECT CAST(MAX(received_at) AS DATE) - INTERVAL 6 DAY AS start "
        "FROM invoices"
    )
    start = row["start"].date().isoformat()
    (counted,) = world.sql(
        f"SELECT COUNT(DISTINCT i.id) AS n FROM invoices i "
        f"JOIN findings f ON f.invoice_id = i.id "
        f"WHERE i.received_at >= '{start}' AND i.received_at < '{end}'"
    )
    (received,) = world.sql(
        f"SELECT COUNT(*) AS n FROM invoices "
        f"WHERE received_at >= '{start}' AND received_at < '{end}'"
    )
    return {
        "value": counted["n"],
        "received": received["n"],
        "window": [start, end],
    }
