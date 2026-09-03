"""Deterministic post-generation SQL lint: the interval-arithmetic check.

The post-coverage bank's W3 rep 4 (2026-09-02): `r.at - rr.at AS
time_in_seconds` in a CTE, then `AVG(time_in_seconds) / 86400 AS
avg_time_in_days`. Both are INTERVALs in DuckDB — a timestamp minus a
timestamp is an interval, and dividing an interval by 86400 yields a
smaller interval (41,667 µs, serialized "0:00:00.041667"), not a
number of days. The cell humanized honestly to "0 seconds" and
verified: the SQL was wrong by a factor of 86,400 and no guard could
see interval arithmetic.

What fires: a select item whose expression tree multiplies or divides
an interval-typed expression by a numeric literal. Interval-typed
means the difference of two timestamp columns (resolved through CTEs
and derived tables by the select-list parse — W3's subtraction sat in
an inner scope with the scaling outside), or an aggregate of one;
AGE(a, b) parses as a - b. Silent for the correct shapes: EPOCH(a - b)
scaled (EPOCH is numeric-valued to the parse since the guard pass, so
the scaling is over a number), DATE_DIFF('hour', ...), JULIAN(a) -
JULIAN(b), arithmetic over numeric columns, and AVG(a - b) unscaled
(an interval renders as a clock string and humanizes correctly). The
scaling INSIDE a numeric function — EPOCH((a - b) / 3600) — is the
same defect and is challenged, since the arguments stay visible.
Outside the parse, and deliberately so: NOW() - col and one-argument
AGE (wall-clock SQL the coverage line already steers away from),
EXTRACT(EPOCH FROM ...) (its inner FROM ends the select-list scan),
and a literal added to an interval (a DuckDB type error the repair
loop sees anyway).

Like the fan-out and enum lints, its word is a repair round with a
license to resend unchanged; run_sql records an overridden challenge
on the executed attempt and the Verifier turns it into a plausibility
warn, so a scaled interval ships [UNVERIFIED] at most.

Pure code: no ports, no I/O.
"""

import re

from engine.substrates.models import DictionaryRow
from engine.tools.durations import is_timestamp_type
from engine.tools.sql_select import (
    Aggregate,
    Arith,
    Column,
    Expr,
    Number,
    Numeric,
    resolve_select_items,
)

# Aggregates whose value keeps the argument's type: an average of
# intervals is an interval. COUNT is a number and never qualifies.
_INTERVAL_AGGREGATES = {"avg", "sum", "min", "max"}

_TimestampColumns = tuple[set[tuple[str, str]], set[str]]


def lint_interval_arithmetic(
    sql: str, dictionary: list[DictionaryRow]
) -> str | None:
    """The reason the statement scales a timestamp difference by a
    number as if the difference were one, or None."""
    typed = _timestamp_columns(dictionary)
    if not typed[0]:
        return None
    scaled = [
        alias
        for alias, expr in resolve_select_items(sql).items()
        if _scales_an_interval(expr, typed)
    ]
    if not scaled:
        return None
    listed = ", ".join(f"`{alias}`" for alias in scaled)
    return (
        f"Interval-arithmetic check: {listed} scales a timestamp "
        "difference by a numeric literal. `a - b` over timestamps is an "
        "INTERVAL; dividing it by 86400 yields a smaller interval, not a "
        "number of days — take EPOCH(a - b) (seconds) first, then scale, "
        "or count units with DATE_DIFF('hour', b, a). If the interval "
        "arithmetic is deliberate, resend the statement unchanged."
    )


def _timestamp_columns(dictionary: list[DictionaryRow]) -> _TimestampColumns:
    """(qualified, unqualified): the (table, column) pairs the dictionary
    types as timestamps, and the bare column names that are timestamps
    in every table carrying them — the only names an unqualified
    reference can be trusted to type."""
    qualified: set[tuple[str, str]] = set()
    seen: dict[str, set[bool]] = {}
    for row in dictionary:
        if not row.column_name:
            continue
        name = row.column_name.lower()
        is_timestamp = is_timestamp_type(row.data_type)
        seen.setdefault(name, set()).add(is_timestamp)
        if is_timestamp:
            qualified.add((row.table_name.lower(), name))
    unqualified = {name for name, flags in seen.items() if flags == {True}}
    return qualified, unqualified


def _is_timestamp(expr: Expr, typed: _TimestampColumns) -> bool:
    qualified, unqualified = typed
    if not isinstance(expr, Column):
        return False
    column = expr.column.lower()
    if expr.table is not None:
        return (expr.table.lower(), column) in qualified
    return column in unqualified


def _is_interval(expr: Expr, typed: _TimestampColumns) -> bool:
    if isinstance(expr, Arith) and expr.op == "-":
        return _is_timestamp(expr.left, typed) and _is_timestamp(expr.right, typed)
    if (
        isinstance(expr, Aggregate)
        and expr.func in _INTERVAL_AGGREGATES
        and expr.arg is not None
    ):
        return _is_interval(expr.arg, typed)
    return False


def _scales_an_interval(expr: Expr, typed: _TimestampColumns) -> bool:
    if isinstance(expr, Arith):
        if expr.op in "*/" and (
            (isinstance(expr.right, Number) and _is_interval(expr.left, typed))
            or (isinstance(expr.left, Number) and _is_interval(expr.right, typed))
        ):
            return True
        return _scales_an_interval(expr.left, typed) or _scales_an_interval(
            expr.right, typed
        )
    if isinstance(expr, Aggregate) and expr.arg is not None:
        return _scales_an_interval(expr.arg, typed)
    if isinstance(expr, Numeric):
        return any(_scales_an_interval(arg, typed) for arg in expr.args)
    return False


# Kept importable for the Verifier's lexical fallback and for tests
# that want the same word list the lint uses.
AGGREGATE_WORD = re.compile(r"\b(avg|sum|min|max)\s*\(", re.IGNORECASE)
