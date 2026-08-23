"""MT1 gold: total invoices, then the per-month breakdown the
pronoun turn must resolve against (addendum SS6: 674/682/634)."""


def gold(world):
    (total,) = world.sql("SELECT COUNT(*) AS n FROM invoices")
    months = {
        row["m"]: row["n"]
        for row in world.sql(
            "SELECT strftime(received_at, '%Y-%m') AS m, COUNT(*) AS n "
            "FROM invoices GROUP BY m"
        )
    }
    return {
        "total": total["n"],
        "march": months["2026-03"],
        "april": months["2026-04"],
        "may": months["2026-05"],
    }
