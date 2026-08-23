"""S6 gold (Story 6): the creepback chain — revision 3 of the
correction-accepting supplier's invoice, flagged with the amount
(92-80) x 10."""


def gold(world):
    rows = world.sql(
        "SELECT i.invoice_number, i.revision, f.rule_name, f.amount "
        "FROM findings f JOIN invoices i ON i.id = f.invoice_id "
        "WHERE f.rule_name LIKE '%creepback%' "
        "ORDER BY f.rule_name"
    )
    return {
        "invoice": rows[0]["invoice_number"],
        "revision": rows[0]["revision"],
        "amount": rows[0]["amount"],
        "finding_rules": [row["rule_name"] for row in rows],
    }
