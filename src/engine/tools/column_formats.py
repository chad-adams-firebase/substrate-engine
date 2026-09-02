"""Which result columns carry a display format (§10.5; NP3; Block 2's
durations; the coverage pass's rates and parse-first resolution).

run_sql's result columns are LLM-chosen aliases over a schema the
engine does not know, so the answer to "is this column money, a
duration, a rate?" comes from the statement and from pack-owned config,
never an engine list (CLAUDE.md: config over code):

- the shared select-list parse (engine.tools.sql_select) — what the
  column was computed FROM: `AVG(invoices.opportunity)` inherits the
  source column's format, `extended_price - amount` is money because
  both sides are, `SUM(amount) / SUM(invoice_total)` is not;
- the Dictionary Map's column_formats (the pack author's money columns)
  and the pack's display.money globs and marker tokens;
- the pack's display.duration and display.rate alias globs (durations
  and rates are computed aliases, not schema columns).

Precedence, stated once. For each result column: (1) the parse — an
item that classifies as money is money; an item that resolves to one
source column has that column's NAME tested against the money list,
then the duration globs, then the rate globs; (2) only when the parse
yields nothing is the alias's own spelling tested by the same rules —
and a parse that has ruled money OUT (a COUNT, a ratio of two money
sums) keeps it out. Money is decided before duration before rate at
every step, so an alias that reads as two things keeps its currency
(avg_unit_rate).

How this relates to the Verifier's resolver: both read the same parse.
The Verifier acts only on aliases that are one real-table leaf in the
outer scope (its documented shapes); the display acts on any
classifiable item. Where both act they name the same source column,
because there is exactly one parse; where the parse is Opaque the
display falls back to alias spelling and the Verifier stays silent.
The fallback never overrides a parse result, so the two cannot disagree
on a parse-resolvable column.

Pure code, unit-tested: no ports, no I/O.
"""

from fnmatch import fnmatchcase

from engine.config.models import DurationSettings, MoneySettings, RateSettings
from engine.substrates.models import DictionaryMap
from engine.tools.envelope import ColumnFormat
from engine.tools.sql_select import (
    Aggregate,
    Arith,
    Column,
    Expr,
    Number,
    resolve_select_items,
    source_column,
)


def money_column_names(dictionary_map: DictionaryMap) -> set[str]:
    """Bare column names the map declares as money — bare because a
    result alias carries no table."""
    return {
        column.split(".", 1)[1].lower() if "." in column else column.lower()
        for rule in dictionary_map.column_formats
        if rule.format == "money"
        for column in rule.columns
    }


def _money_suffix_length(tokens: list[str], money_columns: set[str]) -> int:
    """Token count of the money-column name the alias ends in, or 0.
    total_opportunity ends in opportunity; invoice_total_rank ends in
    rank. A money column name may itself be several tokens
    (invoice_total), so compare token suffixes, not the last token.
    The length matters to the caller: marker tokens veto only when
    they sit BEFORE the matched suffix — "rate" inside avg_unit_rate
    is part of the money column unit_rate, not a marker (the play
    pass's _rate veto-ordering bug), while count_opportunity's
    "count" is a genuine veto."""
    best = 0
    for name in money_columns:
        name_tokens = name.split("_")
        if len(name_tokens) < len(tokens) and tokens[-len(name_tokens):] == name_tokens:
            best = max(best, len(name_tokens))
    return best


def _money_format(
    lowered: str, money_columns: set[str], money: MoneySettings | None
) -> ColumnFormat | None:
    """A column is money when (a) it IS a declared money column, (b)
    its alias ends in one and no token BEFORE that suffix is a
    non-money marker, or (c) it matches a configured pattern."""
    if money is None:
        return None
    markers = {marker.lower() for marker in money.non_money_markers}
    tokens = lowered.split("_")
    suffix_length = _money_suffix_length(tokens, money_columns)
    is_money = (
        lowered in money_columns
        or (suffix_length > 0 and not (set(tokens[:-suffix_length]) & markers))
        or any(fnmatchcase(lowered, pattern.lower()) for pattern in money.column_patterns)
    )
    return ColumnFormat(kind="money", symbol=money.symbol) if is_money else None


def _duration_format(
    lowered: str, duration: DurationSettings | None
) -> ColumnFormat | None:
    """A column is a duration when its alias matches a configured glob;
    the list it matched names the unit its numbers count (None for
    clock-string columns, whose cells carry their own)."""
    if duration is None:
        return None
    for unit, patterns in duration.unit_patterns():
        if any(fnmatchcase(lowered, pattern.lower()) for pattern in patterns):
            return ColumnFormat(kind="duration", unit=unit)
    return None


def _rate_format(lowered: str, rate: RateSettings | None) -> ColumnFormat | None:
    """A column is a rate when its alias matches a configured glob; the
    list it matched names the scale its numbers are on."""
    if rate is None:
        return None
    for scale, patterns in rate.scale_patterns():
        if any(fnmatchcase(lowered, pattern.lower()) for pattern in patterns):
            return ColumnFormat(kind="rate", scale=scale)
    return None


def _by_name(
    lowered: str,
    money_columns: set[str],
    money: MoneySettings | None,
    duration: DurationSettings | None,
    rate: RateSettings | None,
    *,
    allow_money: bool = True,
) -> ColumnFormat | None:
    """The name-based rules in precedence order: money, duration, rate."""
    hint = _money_format(lowered, money_columns, money) if allow_money else None
    return hint or _duration_format(lowered, duration) or _rate_format(lowered, rate)


def money_class(expr: Expr, money_columns: set[str]) -> bool | None:
    """Whether an expression carries dollars: True (money), False (known
    not to — a count, a literal, a non-money column, a ratio of two
    money terms), or None when the parse cannot say (Opaque, or a shape
    with no meaning such as money plus a count)."""
    if isinstance(expr, Column):
        return expr.column in money_columns
    if isinstance(expr, Number):
        return False
    if isinstance(expr, Aggregate):
        if expr.func == "count" or expr.arg is None:
            return False
        return money_class(expr.arg, money_columns)
    if isinstance(expr, Arith):
        left = money_class(expr.left, money_columns)
        right = money_class(expr.right, money_columns)
        if left is None or right is None:
            return None
        if expr.op in "+-":
            return True if left and right else (False if not left and not right else None)
        if expr.op == "*":
            return None if left and right else (left or right)
        # Division: money over a count or literal stays money; money
        # over money is a share; a count over money means nothing.
        if left and right:
            return False
        if left and not right:
            return True
        return False if not left and not right else None
    return None


def _from_expression(
    expr: Expr,
    money_columns: set[str],
    money: MoneySettings | None,
    duration: DurationSettings | None,
    rate: RateSettings | None,
) -> tuple[ColumnFormat | None, bool]:
    """(hint, money_ruled_out) for a parsed select item. The hint comes
    from the expression's money class, else from its single source
    column's name; money_ruled_out tells the alias fallback to skip
    the money rules when the parse has already said no."""
    klass = money_class(expr, money_columns)
    if klass is True and money is not None:
        return ColumnFormat(kind="money", symbol=money.symbol), False
    ruled_out = klass is False
    source = source_column(expr)
    if source is not None:
        return (
            _by_name(
                source.column, money_columns, money, duration, rate,
                allow_money=not ruled_out,
            ),
            ruled_out,
        )
    return None, ruled_out


def resolve_column_formats(
    columns: list[str],
    money_columns: set[str],
    money: MoneySettings | None,
    duration: DurationSettings | None = None,
    rate: RateSettings | None = None,
    *,
    sql: str | None = None,
) -> dict[str, ColumnFormat]:
    """The column_formats a Table carries: parse first (when the SQL is
    in hand), alias spelling second. No settings, no formatting."""
    items = resolve_select_items(sql) if sql else {}
    formats: dict[str, ColumnFormat] = {}
    for column in columns:
        hint: ColumnFormat | None = None
        allow_money = True
        expr = items.get(column)
        if expr is not None:
            hint, ruled_out = _from_expression(expr, money_columns, money, duration, rate)
            allow_money = not ruled_out
        if hint is None:
            hint = _by_name(
                column.lower(), money_columns, money, duration, rate,
                allow_money=allow_money,
            )
        if hint is not None:
            formats[column] = hint
    return formats
