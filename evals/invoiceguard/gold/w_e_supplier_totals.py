"""W-E gold (Polish Pass): per supplier, the invoice total and the
invoice-line total, each aggregated in its own scope — the flagship
table's correct shape. Suppliers with no invoices carry NULL in both
columns and render as a dash; the dash count is a number the bank
asserts like any other."""


def gold(world):
    rows = world.sql(
        "SELECT s.name AS supplier, "
        "(SELECT SUM(i.invoice_total) FROM invoices i WHERE i.supplier_id = s.id) "
        "AS total_invoice_amount, "
        "(SELECT SUM(il.extended_price) FROM invoices i "
        " JOIN invoice_lines il ON i.id = il.invoice_id "
        " WHERE i.supplier_id = s.id) AS total_line_amount "
        "FROM suppliers s ORDER BY total_invoice_amount DESC NULLS LAST"
    )
    top = rows[0]
    null_cells = sum(
        (row["total_invoice_amount"] is None) + (row["total_line_amount"] is None)
        for row in rows
    )
    return {
        "supplier": top["supplier"],
        "total_invoice_amount": top["total_invoice_amount"],
        "total_line_amount": top["total_line_amount"],
        "supplier_rows": len(rows),
        "null_rows": sum(row["total_invoice_amount"] is None for row in rows),
        "null_cells": null_cells,
    }
