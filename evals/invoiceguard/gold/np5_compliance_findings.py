"""NP5 gold: the external compliance layer's findings arrive under a
rule-name prefix convention."""


def gold(world):
    (row,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings "
        "WHERE rule_name LIKE 'compliance_%'"
    )
    return {"value": row["n"]}
