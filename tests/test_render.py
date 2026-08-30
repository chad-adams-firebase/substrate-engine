"""format_cell: the one rule every text surface renders a cell by
(§10.5; NP3). A money cell reads as currency everywhere and a float
tail reaches no human."""

import pytest

from engine.harness.render import format_cell, format_money
from engine.tools.envelope import ColumnFormat

MONEY = ColumnFormat(kind="money", symbol="$")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8308.92139244107, "$8,308.92"),
        (120.0, "$120.00"),
        (120, "$120.00"),
        (1234567.5, "$1,234,567.50"),
        (-1234.0, "-$1,234.00"),
        (0, "$0.00"),
        (0.005, "$0.01"),
    ],
)
def test_money_cells_render_as_currency(value, expected):
    assert format_cell(value, MONEY) == expected
    assert format_money(value, "$") == expected


def test_symbol_is_whatever_the_pack_says():
    assert format_cell(12.5, ColumnFormat(kind="money", symbol="EUR ")) == "EUR 12.50"


def test_unhinted_cells_keep_the_placeholder_rule():
    assert format_cell(146) == "146"
    assert format_cell(0.15) == "0.15"
    assert format_cell(True) == "true"
    assert format_cell(None) == ""
    assert format_cell("RVX01") == "RVX01"
    assert format_cell(8308.92139244107) == "8308.92139244107"


def test_money_hint_never_touches_non_numeric_cells():
    assert format_cell("n/a", MONEY) == "n/a"
    assert format_cell(None, MONEY) == ""
    assert format_cell(True, MONEY) == "true"
