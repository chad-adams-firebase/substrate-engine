"""MT4 gold (Phase 5 Block 4): the supplier with the highest total
invoice amount (code and display name) and that total — turn 1 states
it, turn 4 recalls it through the running summary — and the invoice
count turn 2 asks for in between."""


def gold(world):
    rows = world.sql(
        "SELECT s.code, s.name, SUM(i.invoice_total) AS total_invoice_amount "
        "FROM invoices i JOIN suppliers s ON s.id = i.supplier_id "
        "GROUP BY s.code, s.name ORDER BY total_invoice_amount DESC LIMIT 1"
    )
    (count,) = world.sql("SELECT COUNT(*) AS n FROM invoices")
    return {
        "supplier": rows[0]["code"],
        "supplier_name": rows[0]["name"],
        "total_invoice_amount": rows[0]["total_invoice_amount"],
        "invoice_total": count["n"],
    }
