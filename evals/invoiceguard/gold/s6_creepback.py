"""S6 gold (Story 6): the creepback chain — revision 3 of the
correction-accepting supplier's invoice, flagged with the amount
(92-80) x 10.

Returns the supplier's code and display name as well: the question
asks which supplier, and the grounding mandate (f709d9c) joins ids to
names, so a correct answer may say either (the b3d8375 pattern)."""


def gold(world):
    rows = world.sql(
        "SELECT i.invoice_number, i.revision, f.rule_name, f.amount, "
        "s.code AS supplier_code, s.name AS supplier_name "
        "FROM findings f JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name LIKE '%creepback%' "
        "ORDER BY f.rule_name"
    )
    return {
        "supplier": rows[0]["supplier_code"],
        "supplier_name": rows[0]["supplier_name"],
        "invoice": rows[0]["invoice_number"],
        "revision": rows[0]["revision"],
        "amount": rows[0]["amount"],
        "finding_rules": [row["rule_name"] for row in rows],
    }
