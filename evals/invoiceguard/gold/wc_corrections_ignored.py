"""W-C gold (Play Session #2): the supplier that ignores the most
corrections, from the app's own correction_ignored findings — one per
ignored correction line, on the successor invoice. world_total is the
decoy's ceiling: the session's 28 exceeded every such finding in
existence."""


def gold(world):
    rows = world.sql(
        "SELECT s.name AS supplier, s.code AS code, COUNT(*) AS n "
        "FROM findings f JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name = 'correction_ignored' "
        "GROUP BY s.name, s.code ORDER BY n DESC, s.code"
    )
    top = rows[0]
    return {
        "supplier": top["supplier"],
        "code": top["code"],
        "value": top["n"],
        "world_total": sum(row["n"] for row in rows),
    }
