"""Deterministic post-generation SQL lint: the enum-literal check.

Play Session #2's R-A: "List audit rejections by reviewer" became
`WHERE to_status = 'REJECTED'` — a value invoice_history.to_status never
holds — and shipped zero rows under a generic unverified badge, with
the one useful sentence ("this column never takes that value; here is
what it does take") nowhere on screen. The dictionary already knows the
answer: its data-scan enums list every observed value of a low-
cardinality column (complete up to enum_scan_max_distinct — unlike the
stats substrate's top_values, which stop at five).

What fires: a column that resolves to a dictionary enum and is compared
(`=` or `IN`) ONLY against values the enum never holds — a filter that
is guaranteed to match nothing. A column compared against at least one
observed value is silent, however many never-observed values sit
beside it: those members are no-ops, exactly like the inequality case
(a nonexistent value in <> filters nothing), and the post-duration
bank's AMB2 showed why that matters. "How many invoices are open?"
drafted `invoices.status IN ('RECEIVED', 'READY', 'CLAIMED',
'IN_REVIEW')` — the lifecycle's non-terminal states, read from the
grounding's own value lists — which returns the correct 78: the
resting-status column happens to show only two of the four (the
data-scan enum versus the lifecycle, the bank's NP6 tension), so the
other two match nothing and the query is right. The lint of the day
challenged the two, and the repair round produced a wrong answer
(below). A repair round on a correct query is an invitation to change
something; the lint speaks only where the query cannot be right. The
cost accepted: a typo beside an observed value (`IN ('READY',
'CLOSD')`) undercounts silently — the grounding's full value list is
what prevents it (docs/pin-pass-residuals.md, guard pass).

What the challenge says: the column, the values it does take, and
"keep the query on this table". It never names where else the literal
is observed. The coverage pass ruled the other way (an unobserved
lifecycle value "should say where it lives"), and AMB2's rep 1 read
that sentence as an instruction: `invoices.status` never takes
'RECEIVED' … 'RECEIVED' is an observed value of `invoice_history
.to_status` became `COUNT(*) FROM invoice_history WHERE to_status IN
(...)` — 6,432 transitions, verified, against 1,990 invoices in
existence. A challenge names what is wrong with the query; it never
suggests a different subject table. The grounding prompt already
renders every enum column with its full value list, so a model that
wants to know where a value lives has it; the challenge adds nothing
by repeating it and, live, added a destination.

Silent when the column carries no enum, when the column cannot be
resolved to one dictionary table, and for inequalities. Like the
fan-out lint, its word is a repair round with a license to resend
unchanged; run_sql records an overridden challenge on the executed
attempt and the Verifier turns it into a plausibility warn, so an
empty result from a nonexistent value ships [UNVERIFIED] for a stated
reason, never verified.

Regex-level on purpose (the house precedent, sql_lint.py). Pure code.
"""

import re

from engine.substrates.models import DictionaryRow
from engine.tools.sql_lint import split_scopes, table_aliases, unquote_identifiers

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
    """The reason the statement filters a column only on values it
    never holds, or None."""
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

    # Every literal each enum column is compared against, statement-
    # wide and in order of appearance: the unit of judgement is the
    # column, so `x = 'A' OR x = 'B'` reads like `x IN ('A', 'B')`.
    compared: dict[tuple[str, str], list[str]] = {}
    scannable = _LINE_COMMENT.sub("", unquote_identifiers(sql))
    for match in _COMPARISON.finditer(scannable):
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
        if (table, column) not in enum_of:
            continue
        literals = (
            [_unquote(single)]
            if single is not None
            else [_unquote(lit) for lit in _LITERAL_ONLY.findall(several)]
        )
        seen = compared.setdefault((table, column), [])
        seen.extend(lit for lit in literals if lit not in seen)

    challenges: list[str] = []
    for (table, column), literals in compared.items():
        observed = enum_of[(table, column)]
        if any(literal in observed for literal in literals):
            continue  # at least one member matches: the rest are no-ops
        listed = ", ".join(f"'{literal}'" for literal in literals)
        challenges.append(
            f"Enum check: `{table}.{column}` never takes {listed} in this "
            f"data — observed values: {', '.join(observed)}. Keep the "
            f"query on `{table}`: choose among its observed values, or "
            "ask the user which they meant."
        )
    if not challenges:
        return None
    return " ".join(challenges) + (
        " A filter on a value the column never holds returns no rows. If "
        "the literal is deliberate, resend the statement unchanged."
    )
