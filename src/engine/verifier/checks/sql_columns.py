"""Select-list resolution: map a result column back to the stats
column it was computed from, from the SQL itself.

The display layer guesses from alias spelling (column_formats' token
suffixes); this module doesn't guess — it reads the statement. A
result column resolves only when its select item is one of three
shapes: SUM(col) / SUM(COALESCE(col, <literal>)), AVG(col), or a
plain col [AS alias]. Anything else (CASE, arithmetic, functions,
CTE references) is unresolvable and therefore unchecked — a
documented limitation in the house style of the fan-out lint, not a
hidden one.

Only the OUTER scope's select list is read: the result columns are
its aliases. A statement whose outer FROM is a CTE or subquery
resolves nothing (the placeholder scope carries no real tables).

COALESCE is accepted for SUM only — COALESCE(col, 0) does not change
a sum (the play pass's W1 wrote exactly this shape) — and rejected
for AVG, where substituting a literal changes the population and the
[min, max] bound no longer applies.
"""

import re
from dataclasses import dataclass
from typing import Literal

from engine.tools.sql_lint import select_list_of, split_scopes, table_aliases

_AGG_BARE = re.compile(
    r"^\s*(sum|avg)\s*\(\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*\)"
    r"\s+as\s+([A-Za-z_]\w*)\s*$",
    re.IGNORECASE,
)
_SUM_COALESCE = re.compile(
    r"^\s*(sum)\s*\(\s*coalesce\s*\(\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)"
    r"\s*,[^()]*\)\s*\)\s+as\s+([A-Za-z_]\w*)\s*$",
    re.IGNORECASE,
)
_PLAIN = re.compile(
    r"^\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)"
    r"(?:\s+as\s+([A-Za-z_]\w*))?\s*$",
    re.IGNORECASE,
)
_NON_COLUMN = {"distinct", "null", "true", "false"}


@dataclass
class ResolvedColumn:
    """One result column traced to its source column. table is the
    real table name when the reference was qualified, or None when
    the column must be found among the queried tables' stats."""

    alias: str
    table: str | None
    column: str
    aggregate: Literal["sum", "avg"] | None


def _split_items(select_list: str) -> list[str]:
    """Top-level comma split, paren-aware (function calls survive)."""
    items: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(select_list):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(select_list[start:index])
            start = index + 1
    items.append(select_list[start:])
    return items


def resolve_select_columns(sql: str) -> dict[str, ResolvedColumn]:
    """Result-column alias -> ResolvedColumn for every select item the
    grammar above can trace. Aliases keep their spelling; table and
    column names are lowercased like the stats lookups downstream."""
    scopes = split_scopes(sql)
    if not scopes:
        return {}
    outer = scopes[-1]
    aliases = table_aliases(outer)
    resolved: dict[str, ResolvedColumn] = {}
    for item in _split_items(select_list_of(outer)):
        match = _AGG_BARE.match(item) or _SUM_COALESCE.match(item)
        if match:
            func, qualifier, column, alias = match.groups()
            aggregate: Literal["sum", "avg"] | None = func.lower()  # type: ignore[assignment]
        else:
            plain = _PLAIN.match(item)
            if plain is None:
                continue
            qualifier, column, alias = plain.groups()
            if column.lower() in _NON_COLUMN or (
                alias and alias.lower() in _NON_COLUMN
            ):
                continue
            alias = alias or column
            aggregate = None
        table = aliases.get(qualifier.lower()) if qualifier else None
        if qualifier and table is None:
            continue  # a qualifier the FROM clause never introduced
        resolved.setdefault(
            alias,
            ResolvedColumn(
                alias=alias,
                table=table,
                column=column.lower(),
                aggregate=aggregate,
            ),
        )
    return resolved
