"""RT-fires gold: the rule with the most findings — a different
answer from saves-most on purpose; the pair asserts identical ROUTES,
not identical answers."""


def gold(world):
    rows = world.sql(
        "SELECT rule_name, COUNT(*) AS n FROM findings "
        "GROUP BY rule_name ORDER BY n DESC LIMIT 1"
    )
    return {"rule": rows[0]["rule_name"], "count": rows[0]["n"]}
