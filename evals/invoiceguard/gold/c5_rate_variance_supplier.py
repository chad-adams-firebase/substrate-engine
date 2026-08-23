"""C5 gold (Story 1): the supplier most flagged for rate variance."""


def gold(world):
    rows = world.sql(
        "SELECT s.code, COUNT(*) AS n FROM findings f "
        "JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name = 'rate_variance' "
        "GROUP BY s.code ORDER BY n DESC LIMIT 2"
    )
    return {
        "supplier": rows[0]["code"],
        "count": rows[0]["n"],
        "runner_up": rows[1]["code"],
        "runner_up_count": rows[1]["n"],
    }
