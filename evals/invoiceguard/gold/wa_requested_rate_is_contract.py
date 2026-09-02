"""W-A gold (Play Session #2): the rate auditors ask for is the active
contract rate — every rate correction, not a percentage. The contracts
table is versioned, so the active row is effective_to IS NULL; a naive
join returns 92 rows for 85 lines."""


def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(DISTINCT rrl.id) AS rate_corrections, "
        "COUNT(DISTINCT CASE WHEN c.contract_rate = rrl.requested_rate "
        "THEN rrl.id END) AS at_contract_rate "
        "FROM review_report_lines rrl "
        "JOIN review_reports rr ON rr.id = rrl.review_report_id "
        "JOIN invoices inv ON inv.id = rr.invoice_id "
        "JOIN invoice_lines il ON il.invoice_id = inv.id "
        "AND il.line_number = rrl.line_number "
        "LEFT JOIN contracts c ON c.supplier_id = inv.supplier_id "
        "AND c.item_code = il.item_code AND c.effective_to IS NULL "
        "WHERE rrl.requested_rate IS NOT NULL AND rrl.remove_requested = 0"
    )
    return {
        "rate_corrections": row["rate_corrections"],
        "at_contract_rate": row["at_contract_rate"],
    }
