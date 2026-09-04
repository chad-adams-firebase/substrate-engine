"""The summary scrub (Brief §10.3): the figure grammar reads every
form render.py prints and nothing that is not a figure; restated
figures become "(see turn N)", impossible turn references become "(an
earlier turn)", user-typed numbers are allowed, tables contribute no
figures."""

import re

from engine.harness.state import HistoryTurn
from engine.harness.summary import (
    FIGURE,
    build_summary_messages,
    figure_key,
    figure_set,
    scrub_figures,
    summary_problems,
)


def _prose(turn, answer, question="q"):
    return HistoryTurn(turn=turn, question=question, answer=answer, kind="prose")


def _keys(text):
    return [figure_key(m) for m in FIGURE.finditer(text)]


def _answer_keys(text):
    """What an answer stating `text` puts in the figure set: the public
    path, which also drops the digits inside a hyphenated identifier."""
    return set(figure_set([_prose(1, text)]))


def test_the_grammar_reads_every_rendered_form():
    text = (
        "Total $8,308.92, refund -$1,234.00, rate 92.2%, elapsed 1 hour "
        "then 1.1 days and 45 seconds, 1234 rows, 0.15 share, on 2026-05-30, "
        "null —."
    )
    assert _keys(text) == [
        "8308.92", "-1234", "92.2", "1", "1.1", "45", "1234", "0.15", "2026-05-30",
    ]
    surfaces = [m.group() for m in FIGURE.finditer(text)]
    assert "1 hour" in surfaces and "1.1 days" in surfaces and "45 seconds" in surfaces


def test_the_grammar_never_matches_a_word_code_or_reading():
    for text in (
        "item CR147 and evidence e0 in rule_rate_variance",
        "this uses the closed-invoice opportunity reading",
        "feedback-authored findings, per correction, invoice-level",
        "abc123def",
    ):
        assert _keys(text) == [], text
        assert _answer_keys(text) == set(), text
    # A digit run joined to a letter run by a hyphen is a code. FIGURE
    # alone still sees its digits; figure_set never keys them.
    for code in ("SVC-4410", "CR-147", "INV-CRP-0001", "INV-2026-0001"):
        assert _answer_keys(code) == set(), code
        assert _answer_keys(f"item {code}, then {code}.") == set(), code


def test_a_signed_figure_a_range_and_a_date_still_key():
    text = (
        "refund -$1,234.00, drift -12.5%, fee -$5, items 4471-4480 on "
        "2026-05-30, 1,234 rows"
    )
    expected = ["-1234", "-12.5", "-5", "4471", "4480", "2026-05-30", "1234"]
    assert _keys(text) == expected
    assert _answer_keys(text) == set(expected)


def test_a_figure_never_ends_in_a_comma():
    text = "12, then 1,234, and 5,"
    assert [m.group() for m in FIGURE.finditer(text)] == ["12", "1,234", "5"]
    assert _keys(text) == ["12", "1234", "5"]


def test_a_figure_is_keyed_the_same_in_every_surface():
    assert _keys("$8,308.92 8,308.92 8308.92 $8308.92") == ["8308.92"] * 4
    assert _keys("92.2% 92.2 percent 92.20") == ["92.2"] * 3
    assert _keys("1,000 1000 1000.0") == ["1000"] * 3


def test_figure_set_reads_prose_only_and_allows_what_the_user_typed():
    records = [
        _prose(1, "Item 4471 has 12 findings totaling $8,308.92.", question="How is item 4471 doing?"),
        HistoryTurn(
            turn=2,
            question="Show them",
            answer="[table: SELECT amount FROM findings WHERE item = 4471 LIMIT 100]",
            kind="table",
        ),
        _prose(3, "The rate is 92.2%, 12 of them.", question="What share?"),
        HistoryTurn(turn=4, question="Fire them", answer="[refused: 3 reasons]", kind="refuse"),
    ]
    assert figure_set(records) == {"12": 1, "8308.92": 1, "92.2": 3}


def test_an_identifier_in_an_answer_contributes_no_figure():
    assert figure_set([_prose(4, "SVC-4410 was flagged 63 times.")]) == {"63": 4}


def test_summary_problems_names_restated_figures_and_impossible_turns():
    records = [_prose(1, "Invoices has 50 rows, $8,308.92 in all.")]
    problems = summary_problems(
        "In turn 1 the user asked for the row count; the assistant said 50 rows "
        "and 8,308.92 total, and turn 7 covered the rest.",
        records,
        through_turn=2,
    )
    assert problems.figures == ["50", "8,308.92"]
    assert problems.bad_refs == ["turn 7"]
    assert problems.any()
    clean = summary_problems(
        "In turn 1 the user asked for the row count (see turn 1).", records, 2
    )
    assert not clean.any()


def test_scrub_replaces_figures_by_turn_and_blanks_impossible_references():
    records = [
        _prose(1, "Ravenswood Extrusion billed $1,005,028.40 across 146 invoices."),
        _prose(2, "That is 92.2% of the total."),
    ]
    text = (
        "In turn 1 the user asked about Ravenswood Extrusion, which billed "
        "1,005,028.40 over 146 invoices; turn 2 put it at 92.2 percent, and "
        "turns 3–9 said more."
    )
    scrubbed, count = scrub_figures(text, records, through_turn=2)
    assert scrubbed == (
        "In turn 1 the user asked about Ravenswood Extrusion, which billed "
        "(see turn 1) over (see turn 1) invoices; turn 2 put it at (see turn 2), "
        "and (an earlier turn) said more."
    )
    assert count == 4
    assert not re.search(r"\d", scrubbed.replace("turn 1", "").replace("turn 2", ""))


def test_scrub_leaves_turn_references_and_user_numbers_alone():
    records = [_prose(12, "Item 4471 is flagged 90% of the time.", question="What about item 4471?")]
    text = "The user established flag rates for item 4471 in turn 12 (see turn 12)."
    assert scrub_figures(text, records, through_turn=12) == (text, 0)


def test_scrub_leaves_an_identifier_and_its_comma_alone():
    records = [_prose(3, "SVC-4410 covers item 4471.")]
    text = "Turn 3 said SVC-4410, item 4471 was flagged (see turn 3)."
    assert scrub_figures(text, records, through_turn=3) == (
        "Turn 3 said SVC-4410, item (see turn 3) was flagged (see turn 3).",
        1,
    )


def test_scrub_keeps_the_comma_after_a_figure():
    records = [_prose(2, "There are 12 of them.")]
    assert scrub_figures("The count was 12, per turn 2.", records, 2) == (
        "The count was (see turn 2), per turn 2.",
        1,
    )


def test_a_figure_in_two_answers_cites_the_first():
    records = [_prose(3, "There are 50."), _prose(5, "Still 50.")]
    assert scrub_figures("It was 50 then.", records, 5) == ("It was (see turn 3) then.", 1)


def test_summary_messages_label_turns_and_carry_the_previous_summary():
    records = [
        _prose(3, "Invoices has 50 rows.", question="how many rows?"),
        HistoryTurn(turn=4, question="show them", answer="[table: SELECT 1]", kind="table"),
    ]
    messages = build_summary_messages("SYSTEM", "Earlier (see turn 1).", records, 4)
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "SYSTEM"
    user = messages[1].content
    assert "Previous summary (through turn 2):\nEarlier (see turn 1)." in user
    assert "Turn 3 — user: how many rows?" in user
    assert "Turn 3 — assistant: Invoices has 50 rows." in user
    assert "Turn 4 — assistant: [table: SELECT 1]" in user
    assert user.endswith("Write the updated summary through turn 4.")
    first = build_summary_messages("S", "", records, 4)[1].content
    assert "Previous summary: (none yet)" in first
