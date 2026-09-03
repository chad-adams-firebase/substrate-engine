"""W-D gold (Polish Pass): invoices received per calendar day with at
least one arrival — COUNT(*) over the distinct received dates, the
ratio the browser's correct statement computed and the Verifier
refused as a count."""


def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS invoices, COUNT(DISTINCT DATE(received_at)) AS days, "
        "COUNT(*) * 1.0 / COUNT(DISTINCT DATE(received_at)) AS per_day "
        "FROM invoices"
    )
    return {
        "per_day": row["per_day"],
        "days": row["days"],
        "invoices": row["invoices"],
    }
