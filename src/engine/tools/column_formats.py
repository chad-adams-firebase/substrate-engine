"""Which result columns carry a display format (§10.5; NP3).

run_sql's result columns are LLM-chosen aliases over a schema the
engine does not know, so the answer to "is this column money?" comes
from two pack-owned sources and nothing else: the Dictionary Map's
column_formats (the pack author's list of money columns) and the
pack's display.money settings (glob patterns plus the marker tokens
that veto an alias). "Is this column a duration?" comes from the
pack's display.duration alias globs alone — durations are computed
aliases, not schema columns. No engine list — CLAUDE.md, config over
code.

Pure code, unit-tested: no ports, no I/O.
"""

from fnmatch import fnmatchcase

from engine.config.models import DurationSettings, MoneySettings
from engine.substrates.models import DictionaryMap
from engine.tools.envelope import ColumnFormat


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


def resolve_column_formats(
    columns: list[str],
    money_columns: set[str],
    money: MoneySettings | None,
    duration: DurationSettings | None = None,
) -> dict[str, ColumnFormat]:
    """The column_formats a Table carries. Money is decided first, so
    an alias that reads as both keeps its currency. No settings, no
    formatting."""
    formats: dict[str, ColumnFormat] = {}
    for column in columns:
        lowered = column.lower()
        hint = _money_format(lowered, money_columns, money) or _duration_format(
            lowered, duration
        )
        if hint is not None:
            formats[column] = hint
    return formats
