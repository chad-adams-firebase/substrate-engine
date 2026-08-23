"""S2 gold (Story 2): SVC-4410's service-hours flag rate. The join is
composite — (invoice_id, line_number) — because joining on invoice_id
alone cross-multiplies (the documented trap)."""


def gold(world):
    (flagged,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoice_lines l "
        "JOIN findings f ON f.invoice_id = l.invoice_id "
        "AND f.line_number = l.line_number "
        "WHERE l.item_code = 'SVC-4410' "
        "AND f.rule_name = 'service_hours_excessive'"
    )
    (lines,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoice_lines WHERE item_code = 'SVC-4410'"
    )
    return {
        "flagged": flagged["n"],
        "lines": lines["n"],
        "rate": round(flagged["n"] / lines["n"], 4),
    }
