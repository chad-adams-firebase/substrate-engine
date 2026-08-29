"""MT3 gold: the tool-switch anchor — turn 1's supplier (code and
display name, as C5), turn 2's rule source location."""


def gold(world):
    rows = world.sql(
        "SELECT s.code, s.name, COUNT(*) AS n FROM findings f "
        "JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name = 'rate_variance' "
        "GROUP BY s.code, s.name ORDER BY n DESC LIMIT 1"
    )
    (node,) = world.ckg.resolve_suffix("rule_rate_variance")
    return {
        "supplier": rows[0]["code"],
        "supplier_name": rows[0]["name"],
        "rule_file": node.file_path,
        "start_line": node.start_line,
    }
