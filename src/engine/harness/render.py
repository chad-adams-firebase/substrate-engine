"""Cell rendering: the one place a table value becomes text (§10.5).

Numbers travel store to screen untouched (§9.4) — until a human has
to read them. Every text surface (CLI, eval flattening, placeholder
injection) renders through format_cell so a money cell reads
$8,308.92 everywhere and never 8308.92139244107 anywhere, a duration
reads "1.1 days" rather than 1.0806402437502474, and a NULL reads as
an em dash rather than an empty cell; the browser mirrors these
functions byte for byte.

Rounding is deliberately the browser's rule, not Python's: JavaScript
toFixed rounds the exact binary value half-up, Python's format rounds
it half-even, and the two disagree on exact ties (1.25 -> "1.3" vs
"1.2"). _fixed rounds the exact binary value half-up so the engine
and the page print the same digits for every double.

Pure code: no ports, no I/O.
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal

from engine.tools.envelope import ColumnFormat, DurationUnit, RateScale, Table

NULL_CELL = "—"
# A zero-row table says so, on every surface, instead of rendering as
# an empty box (Play Session #2, S-D: an empty result rendered as
# nothing at all).
NO_ROWS = "No rows matched"

_UNIT_SECONDS: dict[DurationUnit, int] = {
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}
# Largest unit first: a duration reads in the biggest unit it fills.
_HUMAN_UNITS = (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1))
# SQLite's time()/strftime output for an elapsed interval: H:MM:SS,
# hours unpadded or padded, optional fraction.
_CLOCK = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)(?:\.(\d+))?$")


def _fixed(value: int | float, places: int) -> str:
    """value to `places` decimals exactly as JavaScript's toFixed:
    the exact binary value, rounded half-up."""
    exact = Decimal(value) if not isinstance(value, bool) else Decimal(int(value))
    return str(exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def format_money(value: int | float, symbol: str) -> str:
    """Thousands separators, two decimals, sign before the symbol."""
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{Decimal(_fixed(abs(value), 2)):,}"


def humanize_seconds(seconds: float) -> str:
    """An elapsed time in the largest unit it fills, one decimal,
    trailing .0 dropped, singular at exactly one: 3600 -> "1 hour",
    93367 -> "1.1 days", 45 -> "45 seconds"."""
    sign = "-" if seconds < 0 else ""
    magnitude = abs(seconds)
    for name, size in _HUMAN_UNITS:
        if magnitude >= size or size == 1:
            amount = _fixed(magnitude / size, 1)
            break
    text = amount[:-2] if amount.endswith(".0") else amount
    label = name if text == "1" else f"{name}s"
    return f"{sign}{text} {label}"


def parse_clock(text: str) -> float | None:
    """Seconds in an H:MM:SS string, or None when the text is not one."""
    match = _CLOCK.match(text.strip())
    if match is None:
        return None
    hours, minutes, seconds, fraction = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        total += float(f"0.{fraction}")
    return float(total)


def format_duration(value: object, unit: DurationUnit | None) -> str | None:
    """A duration cell as text: a number is measured in the column's
    unit; an H:MM:SS string carries its own. None when the cell is
    neither (the caller falls back to the plain rule)."""
    if isinstance(value, str):
        seconds = parse_clock(value)
        return None if seconds is None else humanize_seconds(seconds)
    if unit is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        return humanize_seconds(value * _UNIT_SECONDS[unit])
    return None


def format_rate(value: int | float, scale: RateScale | None) -> str:
    """A rate as a one-decimal percentage: a fraction is shown x100
    (0.9221105527638191 -> 92.2%), a percent-scale cell as it stands
    (92.21 -> 92.2%). A value past 100% still renders (104.8%) — the
    badge is the Verifier's job, not the formatter's."""
    shown = value if scale == "percent" else value * 100
    return f"{_fixed(shown, 1)}%"


def format_cell(value: object, column_format: ColumnFormat | None = None) -> str:
    """A cell as text. None renders as an em dash; a numeric cell under
    a money hint renders as currency, under a rate hint as a
    percentage; a cell under a duration hint renders humanized;
    everything else renders as the JSON scalar it is (ints plain,
    floats shortest round-trip, booleans lowercase) — the same rule
    placeholders have always applied, so prose and tables agree."""
    if value is None:
        return NULL_CELL
    if column_format is not None and column_format.kind == "duration":
        rendered = format_duration(value, column_format.unit)
        if rendered is not None:
            return rendered
    if isinstance(value, str):
        return value
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if column_format is not None and is_number:
        if column_format.kind == "money":
            return format_money(value, column_format.symbol)
        if column_format.kind == "rate":
            return format_rate(value, column_format.scale)
    if isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return str(value)


def render_table_text(table: Table) -> str:
    """The table as aligned monospace text — the CLI's rendering, and
    the server-side twin of the browser's <table>: same cells, same
    hints, same truncation caption, and the same sentence for no rows."""
    if not table.rows:
        return NO_ROWS
    columns = table.columns
    rows = [
        [format_cell(row.get(c), table.column_formats.get(c)) for c in columns]
        for row in table.rows
    ]
    widths = [
        max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
        for i, c in enumerate(columns)
    ]
    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths))
    lines = [header, "  ".join("-" * w for w in widths)]
    lines.extend(
        "  ".join(cell.ljust(w) for cell, w in zip(r, widths)) for r in rows
    )
    if table.truncated:
        lines.append(f"({len(table.rows)} of {table.total_row_count} rows)")
    return "\n".join(lines)
