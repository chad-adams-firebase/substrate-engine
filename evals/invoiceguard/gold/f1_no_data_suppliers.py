"""F1 gold (Play Session #2): the suppliers a per-supplier total has
no data for — two with no invoices at all, four whose invoices are
LAPSED-born with NULL invoice_total and no lines. Their totals are
NULL (a dash), never $0.00."""


def gold(world):
    no_invoices = world.sql(
        "SELECT s.name FROM suppliers s "
        "WHERE NOT EXISTS (SELECT 1 FROM invoices i WHERE i.supplier_id = s.id) "
        "ORDER BY s.name"
    )
    null_totals = world.sql(
        "SELECT s.name FROM suppliers s "
        "WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.supplier_id = s.id) "
        "AND NOT EXISTS (SELECT 1 FROM invoices i "
        "JOIN invoice_lines il ON il.invoice_id = i.id WHERE i.supplier_id = s.id) "
        "ORDER BY s.name"
    )
    return {
        "no_invoice_suppliers": [row["name"] for row in no_invoices],
        "null_total_suppliers": [row["name"] for row in null_totals],
        "null_total_example": null_totals[0]["name"],
    }
