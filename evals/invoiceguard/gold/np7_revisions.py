def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE revision > 1"
    )
    return {"value": row["n"]}
