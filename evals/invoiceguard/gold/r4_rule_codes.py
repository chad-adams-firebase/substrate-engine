"""R4 gold (play pass): the cardinality question count_vs_stats
falsely refused — 10 distinct compliance rule codes over 4,216
compliance_rules rows. The A1 fix compares COUNT(DISTINCT) to the
column's distinct_count, never row_count."""


def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(DISTINCT rule_code) AS value FROM compliance_rules"
    )
    return {"value": row["value"]}
