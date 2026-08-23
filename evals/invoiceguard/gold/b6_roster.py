"""B6/R-row gold: the executed roster — every person the engine could
name. A refusal that names any of them fails."""


def gold(world):
    rows = world.sql("SELECT short_name FROM users ORDER BY short_name")
    return {"roster": [row["short_name"] for row in rows]}
