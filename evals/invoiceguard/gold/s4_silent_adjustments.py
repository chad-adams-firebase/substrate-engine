"""S4 gold (Story 4 gotcha): adjusted totals never flagged as
mismatches — sanctioned adjustments, not errors."""


def gold(world):
    (silent,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices i WHERE i.adjustment_flag "
        "AND NOT EXISTS (SELECT 1 FROM findings f "
        "WHERE f.invoice_id = i.id AND f.rule_name = 'total_mismatch')"
    )
    (flagged,) = world.sql(
        "SELECT COUNT(DISTINCT invoice_id) AS n FROM findings "
        "WHERE rule_name = 'total_mismatch'"
    )
    return {"value": silent["n"], "flagged_mismatches": flagged["n"]}
