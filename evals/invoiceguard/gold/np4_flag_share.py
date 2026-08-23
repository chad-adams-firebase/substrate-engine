def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(DISTINCT f.invoice_id) * 1.0 / "
        "COUNT(DISTINCT i.id) AS share "
        "FROM invoices i LEFT JOIN findings f ON f.invoice_id = i.id"
    )
    return {"share": round(row["share"], 4)}
