"""MT-KEY gold (Backlog Pass, gate verdict §5 turns 19–20): the anchor
invoice — the highest invoice total, unique in the world — and its
history as executed: how many transitions, the one day they all fall
on, and the auditor who worked it. The follow-up's rows are that
invoice's, or the rep answered another invoice's history — which is
what turn 20 did with `invoice_id = 123`."""


def gold(world):
    (top,) = world.sql(
        "SELECT i.id, i.invoice_number, i.invoice_total FROM invoices i "
        "WHERE i.invoice_total = (SELECT MAX(invoice_total) FROM invoices) "
        "ORDER BY i.id LIMIT 1"
    )
    (ties,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices "
        "WHERE invoice_total = (SELECT MAX(invoice_total) FROM invoices)"
    )
    rows = world.sql(
        "SELECT ih.from_status, ih.to_status, ih.actor, ih.at AS at "
        f"FROM invoice_history ih WHERE ih.invoice_id = {int(top['id'])} "
        "ORDER BY ih.at"
    )
    days = sorted({row["at"].date().isoformat() for row in rows})
    people = [
        row["actor"]
        for row in rows
        if row["actor"] and not row["actor"].startswith("system.")
        and row["actor"] != "invoice-parse"
    ]
    return {
        "invoice_number": top["invoice_number"],
        "invoice_total": top["invoice_total"],
        "invoice_id": top["id"],
        "ties": ties["n"],
        "transitions": len(rows),
        "days": days,
        "day": days[0] if len(days) == 1 else "",
        "actor": people[-1] if people else "",
    }
