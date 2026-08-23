"""U5/U6 gold (N6's live test): savings per rule with exceptioned
findings zeroed — the effective figure, not the raw sum."""


def gold(world):
    rows = world.sql(
        "SELECT f.rule_name, "
        "ROUND(SUM(CASE WHEN ff.valid_exception THEN 0 ELSE f.amount END), 2)"
        " AS effective, ROUND(SUM(f.amount), 2) AS raw "
        "FROM findings f "
        "LEFT JOIN finding_feedback ff ON ff.finding_id = f.id "
        "GROUP BY f.rule_name ORDER BY effective DESC LIMIT 1"
    )
    return {
        "rule": rows[0]["rule_name"],
        "effective": rows[0]["effective"],
        "raw": rows[0]["raw"],
    }
