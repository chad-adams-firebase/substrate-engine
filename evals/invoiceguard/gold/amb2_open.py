"""AMB2 gold: 'open' has no status — READY (78) vs everything not
CLOSED (965) are both defensible readings."""


def gold(world):
    (ready,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'READY'"
    )
    (not_closed,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status != 'CLOSED'"
    )
    return {"ready": ready["n"], "not_closed": not_closed["n"]}
