"""C4 gold: invoices in the data-anchored trailing 30 days — the
verified-zero trap's correct answer, under the window convention fix
pass 3 states in grounding: half-open [last_day - N + 1, last_day + 1)
with last_day the data's final received_at day. The same rule B5/A1
already used for "last week"; the earlier 31-day reading (692) was
inconsistent with it."""


def gold(world):
    (row,) = world.sql(
        "SELECT CAST(MAX(received_at) AS DATE) - INTERVAL 29 DAY AS start, "
        "CAST(MAX(received_at) AS DATE) + INTERVAL 1 DAY AS end_ex "
        "FROM invoices"
    )
    start = row["start"].date().isoformat()
    end = row["end_ex"].date().isoformat()
    (counted,) = world.sql(
        f"SELECT COUNT(*) AS n FROM invoices "
        f"WHERE received_at >= '{start}' AND received_at < '{end}'"
    )
    return {"value": counted["n"], "window": [start, end]}
