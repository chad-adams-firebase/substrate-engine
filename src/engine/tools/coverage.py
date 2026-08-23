"""Data-coverage window, resolved from the stats substrate.

The anchor for windowed tools and the router's date guidance: pack
config names the stats columns whose [min, max] define coverage, and
the values come from the machine-generated stats rows — never
wall-clock (the verified-zero gap's root), never dates typed into
config. Naming columns is deliberate: a naive reduce over every
TIMESTAMP column would drag coverage back to the oldest contract
date, years before any operational data.

Resolved once at composition; pure code, no ports.
"""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict

from engine.substrates.models import StatsRow

_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


class CoverageWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date


def _as_date(text: str | None) -> date | None:
    if not text:
        return None
    match = _ISO_PREFIX.match(text.strip())
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def resolve_coverage_window(
    stats: list[StatsRow], columns: list[str]
) -> CoverageWindow | None:
    """Min-of-mins / max-of-maxes over the named "table.column" stats
    rows; None when no columns are configured. A named column that is
    missing from stats or carries unparseable bounds raises — a pack
    that names coverage columns is asserting they exist, and config
    typos fail at build time with the column named."""
    if not columns:
        return None
    by_column = {
        f"{row.table_name}.{row.column_name}": row for row in stats
    }
    starts: list[date] = []
    ends: list[date] = []
    for column in columns:
        row = by_column.get(column)
        if row is None:
            raise ValueError(
                f"coverage column '{column}' has no stats row"
            )
        low, high = _as_date(row.min_value), _as_date(row.max_value)
        if low is None or high is None:
            raise ValueError(
                f"coverage column '{column}' has no parseable date "
                f"bounds (min={row.min_value!r}, max={row.max_value!r})"
            )
        starts.append(low)
        ends.append(high)
    return CoverageWindow(start=min(starts), end=max(ends))
