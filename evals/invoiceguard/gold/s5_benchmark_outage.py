"""S5 gold (Story 5): the benchmark outage day, from the raw log."""


def gold(world):
    fallbacks = world.grep_log(r"event=benchmark_fallback")
    on_day = [line for line in fallbacks if "ts=2026-03-11" in line]
    return {
        "fallback_warnings": len(on_day),
        "all_on_outage_day": len(on_day) == len(fallbacks),
        "date": "2026-03-11",
    }
