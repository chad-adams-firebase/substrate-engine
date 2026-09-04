"""Emitted-token detection: the §7.2 stratification instrument. One
regex set shared by runner and grader, pinned by cases drawn from the
three acceptance sessions' actual failure surfaces."""

import pytest

from engine.eval.tokens import (
    answer_body,
    answer_caption,
    answer_envelope,
    detect,
    extract_numbers,
    flatten_answer,
)
from engine.harness.outcomes import (
    ClarifyOutcome,
    MarkdownAnswer,
    AnswerOutcome,
    RefuseOutcome,
    TableAnswer,
)
from engine.tools.envelope import Table


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        ("defined at lines 116–149 of the file", "line_numbers", ["lines 116–149"]),
        ("see line 84 for the call", "line_numbers", ["line 84"]),
        ("src/invoiceguard/spine/rules_engine.py holds it", "file_paths",
         ["src/invoiceguard/spine/rules_engine.py"]),
        ("the sweep ran on 2026-05-29 at 18:00", "iso_dates", ["2026-05-29"]),
        ("the sweep ran on May 29", "prose_dates", ["May 29"]),
        ("ran on Sept. 4 and October 12", "prose_dates", ["Sept. 4", "October 12"]),
        ("recovering $8,308.92 in total", "money", ["$8,308.92"]),
        ("a raw value of 8308.92139244107", "float_tails", ["8308.92139244107"]),
        ("twelve audit rules fire", "word_numbers", ["twelve"]),
        ("the `rules_engine` component", "backticked", ["rules_engine"]),
    ],
)
def test_detect_cases(text, field, expected):
    assert getattr(detect(text), field) == expected


def test_detect_distinguishes_iso_from_prose():
    tokens = detect("logged 2026-05-29; humans say May 29")
    assert tokens.iso_dates == ["2026-05-29"]
    assert tokens.prose_dates == ["May 29"]


def test_extract_numbers():
    text = "146 of 161 invoices; twelve rules; 95.45%; $8,308.92 total"
    assert extract_numbers(text) == [146, 161, 95.45, 8308.92, 12]


def test_flatten_answer_shapes():
    prose = AnswerOutcome(
        body=MarkdownAnswer(text="146 invoices."), verification="verified"
    )
    assert flatten_answer(prose) == "146 invoices."

    table = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["supplier", "n"],
                rows=[{"supplier": "RVX01", "n": 257}],
                total_row_count=1,
            ),
            caption="SELECT ...",
        ),
        verification="verified",
    )
    flat = flatten_answer(table)
    assert "RVX01 257" in flat and "SELECT ..." in flat

    refuse = RefuseOutcome(reason="no", what_would_work="ask counts")
    assert answer_body(table) == "supplier n\nRVX01 257"
    assert answer_caption(table) == "SELECT ..."
    # Close Pass: the reading line is part of the caption pool (the
    # surfaces show it), never of the body (it carries no value).
    named = table.model_copy(
        update={"body": table.body.model_copy(update={"reading": "closed-invoice opportunity"})}
    )
    assert answer_caption(named) == "Reading: closed-invoice opportunity.\nSELECT ..."
    assert "closed-invoice" in flatten_answer(named)
    assert answer_body(named) == "supplier n\nRVX01 257"
    assert answer_envelope(table) == "table"
    assert answer_envelope(prose) == "markdown"
    assert answer_envelope(refuse) == "refuse"
    assert flatten_answer(refuse) == "no\nask counts"
    assert flatten_answer(ClarifyOutcome(question="which day?")) == "which day?"
    assert flatten_answer(None) == ""


def test_extract_durations():
    """Duration pass: the unit-aware pool — every phrase the humanizer
    can print, plus clock strings, each with half a displayed decimal
    of its own unit as tolerance."""
    from engine.eval.tokens import extract_durations

    text = (
        "About 1 hour (60 minutes); the worst took 1.1 days, the best "
        "twelve seconds, and -3 hours is impossible; 1:00:00 on the clock; "
        "2026-05-29 18:00 is a time, 1,440 minutes a day"
    )
    seconds, tolerances = zip(*extract_durations(text))
    assert seconds == pytest.approx((3600.0, 3600.0, 95040.0, 12.0, -10800.0, 86400.0, 3600.0))
    assert tolerances == pytest.approx((180.0, 3.0, 4320.0, 0.05, 180.0, 3.0, 0.05))
    assert extract_durations("146 invoices in 3 batches") == []
