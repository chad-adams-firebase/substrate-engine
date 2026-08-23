"""Honest-negative gold: the creepback scan verifiably did NOT run
on 2026-05-26 (it ran 05-25 and 05-27) — in coverage, honest no."""


def gold(world):
    lines = world.grep_log(
        r"ts=2026-05-26.*event=creepback_scan_completed"
    )
    neighbors = world.grep_log(
        r"ts=2026-05-2[57].*event=creepback_scan_completed"
    )
    return {
        "ran": len(lines) > 0,
        "count": len(lines),
        "neighbor_runs": len(neighbors),
        "date": "2026-05-26",
    }
