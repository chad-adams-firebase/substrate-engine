def gold(world):
    (row,) = world.sql("SELECT COUNT(*) AS n FROM invoices")
    return {"value": row["n"]}
