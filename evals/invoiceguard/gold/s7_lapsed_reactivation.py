"""S7 gold (Story 7): the one sanctioned LAPSED reactivation and who
performed it."""


def gold(world):
    rows = world.sql(
        "SELECT i.invoice_number, h.actor FROM invoice_history h "
        "JOIN invoices i ON i.id = h.invoice_id "
        "WHERE h.from_status = 'LAPSED'"
    )
    return {
        "count": len(rows),
        "invoice": rows[0]["invoice_number"],
        "actor": rows[0]["actor"],
    }
