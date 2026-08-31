"""W1 gold (play pass, multi-turn extension): the per-supplier table
with invoice-line totals added correctly — line-less suppliers kept
(the fanned INNER JOIN dropped 4), SUM columns at invoice grain (the
fanned join multiplied them ×4–5)."""


def gold(world):
    rows = world.sql(
        "SELECT s.name AS supplier_name, "
        "COUNT(DISTINCT i.id) AS invoice_count, "
        "SUM(i.invoice_total) AS total_invoice_amount, "
        "(SELECT COALESCE(SUM(l.extended_price), 0) FROM invoice_lines l "
        " JOIN invoices i2 ON i2.id = l.invoice_id "
        " WHERE i2.supplier_id = s.id) AS total_line_amount "
        "FROM suppliers s JOIN invoices i ON s.id = i.supplier_id "
        "GROUP BY s.id, s.name ORDER BY invoice_count DESC"
    )
    top = rows[0]
    lineless = [r for r in rows if r["total_line_amount"] == 0]
    return {
        "supplier_name": top["supplier_name"],
        "invoice_count": top["invoice_count"],
        "total_invoice_amount": top["total_invoice_amount"],
        "total_line_amount": top["total_line_amount"],
        "supplier_rows": len(rows),
        "lineless_supplier": lineless[0]["supplier_name"],
    }
