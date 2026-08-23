"""NP6 gold: the observed status enum — 4 of the primer's 7 lifecycle
values persist at end-of-world (the evidence-conflict probe)."""


def gold(world):
    rows = world.sql("SELECT DISTINCT status FROM invoices ORDER BY status")
    return {"statuses": [row["status"] for row in rows]}
