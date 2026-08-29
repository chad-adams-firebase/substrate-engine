"""C5 gold (Story 1): the supplier most flagged for rate variance.

Returns both the supplier's code and its display name: the grounding
mandate (f709d9c) joins ids to names, so a correct answer may say
either "RVX01" or "Ravenswood Extrusion"."""


def gold(world):
    rows = world.sql(
        "SELECT s.code, s.name, COUNT(*) AS n FROM findings f "
        "JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name = 'rate_variance' "
        "GROUP BY s.code, s.name ORDER BY n DESC LIMIT 2"
    )
    return {
        "supplier": rows[0]["code"],
        "supplier_name": rows[0]["name"],
        "count": rows[0]["n"],
        "runner_up": rows[1]["code"],
        "runner_up_count": rows[1]["n"],
    }
