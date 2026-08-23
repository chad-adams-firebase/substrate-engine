def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE is_credit_memo"
    )
    return {"value": row["n"]}
