"""C3 gold: range of invoice totals, live SQL (stats substrate holds
the same numbers; the referee recomputes)."""


def gold(world):
    (row,) = world.sql(
        "SELECT MIN(invoice_total) AS lo, MAX(invoice_total) AS hi "
        "FROM invoices"
    )
    return {"min": row["lo"], "max": row["hi"]}
