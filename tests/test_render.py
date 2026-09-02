"""format_cell: the one rule every text surface renders a cell by
(§10.5; NP3, and Block 2's durations and NULL dash). A money cell
reads as currency everywhere, a duration reads humanized, a NULL is
an em dash, and a float tail reaches no human."""

import pytest

from engine.harness.render import (
    NO_ROWS,
    NULL_CELL,
    format_cell,
    format_money,
    format_rate,
    humanize_seconds,
    parse_clock,
    render_table_text,
)
from engine.tools.envelope import ColumnFormat, Table

MONEY = ColumnFormat(kind="money", symbol="$")
FRACTION = ColumnFormat(kind="rate", scale="fraction")
PERCENT = ColumnFormat(kind="rate", scale="percent")
DAYS = ColumnFormat(kind="duration", unit="days")
HOURS = ColumnFormat(kind="duration", unit="hours")
CLOCK = ColumnFormat(kind="duration")  # H:MM:SS strings carry their unit


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


def test_rounding_is_the_browsers_half_up_on_the_exact_double():
    """JavaScript toFixed and Python's format disagree on exact binary
    ties (0.125 is exactly representable): toFixed says 0.13, format
    says 0.12. The engine follows the browser so both print the same
    digits — the page mirrors format_money byte for byte."""
    assert format_money(0.125, "$") == "$0.13"
    assert format_money(2.675, "$") == "$2.67"  # not a tie: 2.67499999…
    assert humanize_seconds(1.25 * 86400) == "1.3 days"


def test_symbol_is_whatever_the_pack_says():
    assert format_cell(12.5, ColumnFormat(kind="money", symbol="EUR ")) == "EUR 12.50"


@pytest.mark.parametrize(
    ("value", "hint", "expected"),
    [
        (1.0806402437502474, DAYS, "1.1 days"),  # the play session's sighting
        (1.0, DAYS, "1 day"),
        (0.5, DAYS, "12 hours"),
        (2, HOURS, "2 hours"),
        (1.5, HOURS, "1.5 hours"),
        (0.25, HOURS, "15 minutes"),
        (0.01, HOURS, "36 seconds"),
        (0, HOURS, "0 seconds"),
        (-3, HOURS, "-3 hours"),
        ("1:00:00", CLOCK, "1 hour"),  # the play session's other sighting
        ("01:00:00", CLOCK, "1 hour"),
        ("0:30:00", CLOCK, "30 minutes"),
        ("36:00:00", CLOCK, "1.5 days"),
        ("0:00:45.5", CLOCK, "45.5 seconds"),
        ("1:00:00", DAYS, "1 hour"),  # a clock string carries its own unit
    ],
)
def test_duration_cells_render_humanized(value, hint, expected):
    assert format_cell(value, hint) == expected


def test_duration_hint_leaves_non_duration_cells_alone():
    assert format_cell("2026-04-15 10:00:00", CLOCK) == "2026-04-15 10:00:00"
    assert format_cell("n/a", DAYS) == "n/a"
    assert format_cell(True, DAYS) == "true"
    assert format_cell(None, DAYS) == NULL_CELL
    # A clock-string column with a numeric cell has no unit to count in.
    assert format_cell(3600, CLOCK) == "3600"


def test_parse_clock_rejects_anything_but_an_interval():
    assert parse_clock("1:00:00") == 3600.0
    assert parse_clock("1:60:00") is None
    assert parse_clock("10:00") is None
    assert parse_clock("2026-04-15 10:00:00") is None


def test_unhinted_cells_keep_the_placeholder_rule():
    assert format_cell(146) == "146"
    assert format_cell(0.15) == "0.15"
    assert format_cell(True) == "true"
    assert format_cell("RVX01") == "RVX01"
    assert format_cell(8308.92139244107) == "8308.92139244107"


def test_null_cells_are_an_em_dash_never_blank():
    assert format_cell(None) == NULL_CELL == "—"
    assert format_cell(None, MONEY) == NULL_CELL


def test_money_hint_never_touches_non_numeric_cells():
    assert format_cell("n/a", MONEY) == "n/a"
    assert format_cell(True, MONEY) == "true"


def test_table_text_renders_every_hint_and_the_dash():
    table = Table(
        columns=["supplier", "total", "wait", "note"],
        rows=[
            {"supplier": "RVX01", "total": 8308.92139244107, "wait": 1.0806402437502474, "note": None},
            {"supplier": "ACME", "total": 12, "wait": 0.5, "note": "ok"},
        ],
        total_row_count=5,
        truncated=True,
        column_formats={"total": MONEY, "wait": DAYS},
    )
    text = render_table_text(table)
    lines = text.splitlines()
    assert lines[0].split() == ["supplier", "total", "wait", "note"]
    assert "$8,308.92" in lines[2] and "1.1 days" in lines[2] and "—" in lines[2]
    assert "$12.00" in lines[3] and "12 hours" in lines[3] and "ok" in lines[3]
    assert lines[-1] == "(2 of 5 rows)"


@pytest.mark.parametrize(
    ("value", "hint", "expected"),
    [
        (0.9221105527638191, FRACTION, "92.2%"),  # Play Session #2's sighting
        (0.9545454545454546, FRACTION, "95.5%"),
        (1.0, FRACTION, "100.0%"),
        (0.0, FRACTION, "0.0%"),
        (1.0476190476190477, FRACTION, "104.8%"),  # past 100% still renders
        (0.125, FRACTION, "12.5%"),
        (0.0625, FRACTION, "6.3%"),  # exact tie rounds half-up like toFixed
        (92.21, PERCENT, "92.2%"),
        (100, PERCENT, "100.0%"),
        (0.75, PERCENT, "0.8%"),  # a fraction in a percent alias: shown as written
    ],
)
def test_rate_cells_render_as_one_decimal_percentages(value, hint, expected):
    assert format_cell(value, hint) == expected
    assert format_rate(value, hint.scale) == expected


def test_rate_hint_leaves_non_numeric_cells_alone():
    assert format_cell("n/a", FRACTION) == "n/a"
    assert format_cell(True, FRACTION) == "true"
    assert format_cell(None, PERCENT) == NULL_CELL


def test_an_empty_table_says_so_instead_of_rendering_nothing():
    """Play Session #2, S-D: a zero-row result rendered as two blank
    lines here and an empty box in the browser."""
    empty = Table(columns=[], rows=[], total_row_count=0)
    assert render_table_text(empty) == NO_ROWS == "No rows matched"
    with_header = Table(columns=["reviewer", "n"], rows=[], total_row_count=0)
    assert render_table_text(with_header) == NO_ROWS
