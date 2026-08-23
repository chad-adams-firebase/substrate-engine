def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS n FROM suppliers WHERE NOT network"
    )
    (total,) = world.sql("SELECT COUNT(*) AS n FROM suppliers")
    return {"value": row["n"], "suppliers": total["n"]}
