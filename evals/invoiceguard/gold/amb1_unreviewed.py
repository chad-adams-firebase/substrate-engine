"""U3 gold: both total readings of 'go unreviewed per day' — the
current-backlog reading and the never-reviewed LAPSED reading. They
prove the ambiguity is real; the row asserts a named reading."""


def gold(world):
    (ready,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'READY'"
    )
    (lapsed,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'LAPSED'"
    )
    return {"backlog": ready["n"], "lapsed": lapsed["n"]}
