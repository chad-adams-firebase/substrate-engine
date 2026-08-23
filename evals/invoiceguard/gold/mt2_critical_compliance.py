"""MT2 gold (session-3 conv 14): compliance findings, then the
critical-severity subset ('those') — findings map 1:1 onto
compliance_rules rows, whose severity is the authority."""


def gold(world):
    (all_rows,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings "
        "WHERE rule_name LIKE 'compliance_%'"
    )
    (critical,) = world.sql(
        "SELECT COUNT(*) AS n FROM compliance_rules "
        "WHERE severity = 'CRITICAL'"
    )
    return {"compliance": all_rows["n"], "critical": critical["n"]}
