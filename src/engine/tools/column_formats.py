"""Which result columns carry a display format (§10.5; NP3).

run_sql's result columns are LLM-chosen aliases over a schema the
engine does not know, so the answer to "is this column money?" comes
from two pack-owned sources and nothing else: the Dictionary Map's
column_formats (the pack author's list of money columns) and the
pack's display.money settings (glob patterns plus the marker tokens
that veto an alias). No engine list — CLAUDE.md, config over code.

Pure code, unit-tested: no ports, no I/O.
"""

from fnmatch import fnmatchcase

from engine.config.models import MoneySettings
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


def _ends_with_money_column(tokens: list[str], money_columns: set[str]) -> bool:
    """total_opportunity ends in opportunity; invoice_total_rank ends
    in rank. A money column name may itself be several tokens
    (invoice_total), so compare token suffixes, not the last token."""
    for name in money_columns:
        name_tokens = name.split("_")
        if len(name_tokens) < len(tokens) and tokens[-len(name_tokens):] == name_tokens:
            return True
    return False


def resolve_column_formats(
    columns: list[str],
    money_columns: set[str],
    money: MoneySettings | None,
) -> dict[str, ColumnFormat]:
    """The column_formats a Table carries. A column is money when
    (a) it IS a declared money column, (b) its alias ends in one and
    no token is a non-money marker, or (c) it matches a configured
    pattern. No money settings, no formatting."""
    if money is None:
        return {}
    markers = {marker.lower() for marker in money.non_money_markers}
    formats: dict[str, ColumnFormat] = {}
    for column in columns:
        lowered = column.lower()
        tokens = lowered.split("_")
        is_money = (
            lowered in money_columns
            or (
                _ends_with_money_column(tokens, money_columns)
                and not (set(tokens) & markers)
            )
            or any(fnmatchcase(lowered, pattern.lower()) for pattern in money.column_patterns)
        )
        if is_money:
            formats[column] = ColumnFormat(kind="money", symbol=money.symbol)
    return formats
