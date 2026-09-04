"""MT-ANCHOR gold (Backlog Pass, gate verdict §5 turns 6–9): the rule
that fires most — the turn-6 anchor — and its count, beside the rule
the session drifted to and that rule's count, so a turn-3 answer of
the wrong count grades as the contradiction it is."""


def gold(world):
    rows = world.sql(
        "SELECT rule_name, COUNT(*) AS n FROM findings "
        "GROUP BY rule_name ORDER BY n DESC, rule_name LIMIT 2"
    )
    (drifted,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'new_supplier'"
    )
    return {
        "rule": rows[0]["rule_name"],
        "fire_count": rows[0]["n"],
        "runner_up": rows[1]["rule_name"],
        "runner_up_count": rows[1]["n"],
        "wrong_rule": "new_supplier",
        "wrong_count": drifted["n"],
    }
