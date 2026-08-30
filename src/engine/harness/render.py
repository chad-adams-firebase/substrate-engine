"""Cell rendering: the one place a table value becomes text (§10.5).

Numbers travel store to screen untouched (§9.4) — until a human has
to read them. Every text surface (CLI, eval flattening, placeholder
injection) renders through format_cell so a money cell reads
$8,308.92 everywhere and never 8308.92139244107 anywhere; the browser
mirrors this function byte for byte.

Pure code: no ports, no I/O.
"""

import json

from engine.tools.envelope import ColumnFormat


def format_money(value: int | float, symbol: str) -> str:
    """Thousands separators, two decimals, sign before the symbol."""
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def format_cell(value: object, column_format: ColumnFormat | None = None) -> str:
    """A cell as text. None renders empty; a numeric cell under a
    money hint renders as currency; everything else renders as the
    JSON scalar it is (ints plain, floats shortest round-trip,
    booleans lowercase) — the same rule placeholders have always
    applied, so prose and tables agree."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if (
        column_format is not None
        and column_format.kind == "money"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return format_money(value, column_format.symbol)
    if isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return str(value)
