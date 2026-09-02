"""Deterministic post-generation SQL lint: the enum-literal check.

Play Session #2's R-A: "List audit rejections by reviewer" became
`WHERE to_status = 'REJECTED'` — a value invoice_history.to_status never
holds — and shipped zero rows under a generic unverified badge, with
the one useful sentence ("this column never takes that value; here is
what it does take") nowhere on screen. The dictionary already knows the
answer: its data-scan enums list every observed value of a low-
cardinality column (complete up to enum_scan_max_distinct — unlike the
stats substrate's top_values, which stop at five).

What fires: `col = 'LITERAL'` or `col IN ('A', 'B')` where col resolves
to a dictionary column carrying enum_values and the literal is not
among them. The challenge names the observed values and, when another
column's enum does hold the literal, names that column (the coverage
pass ruling: `invoices.status = 'IN_REVIEW'` is a legal lifecycle value
the resting-status column never shows — say where it lives). Silent
when the column carries no enum, when the literal is observed, when
the column cannot be resolved to one dictionary table, and for
inequalities (a nonexistent value in <> is a no-op, not a wrong
answer).

Like the fan-out lint, its word is a repair round with a license to
resend unchanged; run_sql records an overridden challenge on the
executed attempt and the Verifier turns it into a plausibility warn,
so an empty result from a nonexistent value ships [UNVERIFIED] for a
stated reason, never verified.

Regex-level on purpose (the house precedent, sql_lint.py). Pure code.
"""

import re

from engine.substrates.models import DictionaryRow
from engine.tools.sql_lint import split_scopes, table_aliases

_LITERAL = r"'(?:[^']|'')*'"
_COMPARISON = re.compile(
    rf"(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*"
    rf"(?:(?<![<>!])=\s*({_LITERAL})"
    rf"|\bin\s*\(\s*({_LITERAL}(?:\s*,\s*{_LITERAL})*)\s*\))",
    re.IGNORECASE,
)
_LITERAL_ONLY = re.compile(_LITERAL)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _unquote(literal: str) -> str:
    return literal[1:-1].replace("''", "'")


def lint_enum_literals(sql: str, dictionary: list[DictionaryRow]) -> str | None:
    """The reason the statement filters on a value its column never
    holds, or None."""
    enum_of: dict[tuple[str, str], list[str]] = {
        (row.table_name.lower(), row.column_name.lower()): list(row.enum_values)
        for row in dictionary
        if row.column_name and row.enum_values
    }
    if not enum_of:
        return None
    columns_of: dict[str, set[str]] = {}
    for row in dictionary:
        if row.column_name:
            columns_of.setdefault(row.table_name.lower(), set()).add(
                row.column_name.lower()
            )

    # Every alias the statement introduces, across all its scopes; only
    # real dictionary tables count (a CTE name resolves to nothing).
    aliases: dict[str, str] = {}
    for scope in split_scopes(sql):
        for alias, table in table_aliases(scope).items():
            if table in columns_of:
                aliases.setdefault(alias, table)
    queried = set(aliases.values())

    challenges: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _COMPARISON.finditer(_LINE_COMMENT.sub("", sql)):
        qualifier, column, single, several = match.groups()
        column = column.lower()
        if qualifier:
            table = aliases.get(qualifier.lower())
            if table is None:
                continue
        else:
            owners = [t for t in queried if column in columns_of.get(t, set())]
            if len(owners) != 1:
                continue
            table = owners[0]
        observed = enum_of.get((table, column))
        if observed is None:
            continue
        literals = (
            [_unquote(single)]
            if single is not None
            else [_unquote(lit) for lit in _LITERAL_ONLY.findall(several)]
        )
        for literal in literals:
            if literal in observed or (table, column, literal) in seen:
                continue
            seen.add((table, column, literal))
            elsewhere = sorted(
                f"`{t}.{c}`"
                for (t, c), values in enum_of.items()
                if literal in values and (t, c) != (table, column)
            )
            hint = (
                f" '{literal}' is an observed value of {', '.join(elsewhere)}, "
                "if that is the column meant."
                if elsewhere
                else ""
            )
            challenges.append(
                f"Enum check: `{table}.{column}` never takes '{literal}' in "
                f"this data — observed values: {', '.join(observed)}.{hint}"
            )
    if not challenges:
        return None
    return " ".join(challenges) + (
        " A filter on a value the column never holds returns no rows. If "
        "the literal is deliberate, resend the statement unchanged."
    )
