"""U3 gold: both readings of 'go unreviewed per day' — the
current-backlog reading and the never-reviewed LAPSED reading. The
row expects clarify; the readings prove the ambiguity is real."""


def gold(world):
    (ready,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'READY'"
    )
    (lapsed,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'LAPSED'"
    )
    return {"backlog": ready["n"], "lapsed": lapsed["n"]}
