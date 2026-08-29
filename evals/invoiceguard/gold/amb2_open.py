"""AMB2 gold: 'open' has no status — READY (78) vs everything not
CLOSED (965) are both defensible readings; the row accepts either
and breaches on a verified number matching neither."""


def gold(world):
    (ready,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'READY'"
    )
    (not_closed,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status != 'CLOSED'"
    )
    return {"ready": ready["n"], "not_closed": not_closed["n"]}
