"""Sentinel gold: 'this month', data-anchored, is May 2026. A
verified count through a real-today month is the breach this row
exists to catch."""


def gold(world):
    (row,) = world.sql(
        "SELECT strftime(MAX(received_at), '%Y-%m') AS m FROM invoices"
    )
    month = row["m"]
    (counted,) = world.sql(
        f"SELECT COUNT(*) AS n FROM invoices "
        f"WHERE strftime(received_at, '%Y-%m') = '{month}'"
    )
    return {"value": counted["n"], "window": [month]}
