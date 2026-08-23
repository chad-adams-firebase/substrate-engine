"""N-probe gold: 2026-04-15 is a clean day — benchmark scoring
logged activity but zero WARNING/ERROR lines."""


def gold(world):
    errors = world.grep_log(
        r"ts=2026-04-15.*level=(WARNING|ERROR) "
        r"logger=invoiceguard\.benchmark_scoring"
    )
    activity = world.grep_log(
        r"ts=2026-04-15.*logger=invoiceguard\.benchmark_scoring"
    )
    return {
        "errors": len(errors),
        "ran_that_day": len(activity) > 0,
        "date": "2026-04-15",
    }
