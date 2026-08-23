"""C1 gold: the stale sweep's completion event on 2026-05-29, from
the raw log — the same grep the acceptance sessions used."""


def gold(world):
    lines = world.grep_log(
        r"ts=2026-05-29.*logger=invoiceguard\.lapse_lifecycle .*"
        r"event=stale_sweep_completed"
    )
    return {"ran": len(lines) > 0, "count": len(lines), "date": "2026-05-29"}
