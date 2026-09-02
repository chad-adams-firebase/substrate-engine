"""engine/web/render.py: every outcome kind as the page shows it,
rendered server-side so tests can read what the browser draws. The
browser JS is not unit-tested; this pins the card vocabulary the two
share and the rule that engineer detail never reaches a card."""

from engine.harness.outcomes import (
    AnswerOutcome,
    ClarifyOutcome,
    EscalateOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
)
from engine.tools.envelope import ColumnFormat, Table
from engine.web.app import STATIC_DIR
from engine.web.render import (
    CARD_TITLES,
    UNVERIFIED_BADGE,
    card_for,
    render_outcome_text,
)


def test_refusal_card_carries_reason_and_remedy_never_detail():
    outcome = RefuseOutcome(
        reason="I couldn't gather enough evidence to answer this reliably.",
        what_would_work="A narrower question.",
        detail="tool budget exhausted: 8 router steps without a terminal decision",
    )
    card = card_for(outcome)
    assert card.kind == "refuse" and card.title == "This can't be answered"
    assert card.fields == [
        ("Why", outcome.reason),
        ("What would work", "A narrower question."),
    ]
    text = render_outcome_text(outcome)
    assert "8 router steps" not in text and "budget" not in text
    assert text == (
        "This can't be answered\n"
        "Why: I couldn't gather enough evidence to answer this reliably.\n"
        "What would work: A narrower question."
    )


def test_refusal_without_a_remedy_omits_the_line():
    card = card_for(RefuseOutcome(reason="no"))
    assert card.fields == [("Why", "no")]


def test_clarify_and_escalate_cards():
    clarify = card_for(ClarifyOutcome(question="Which week do you mean?"))
    assert clarify.title == "One thing to clarify first"
    assert clarify.fields == [("Question", "Which week do you mean?")]
    escalate = EscalateOutcome(reason="a policy call")
    assert card_for(escalate).title == "This needs a person"
    assert render_outcome_text(escalate) == "This needs a person\nWhy: a policy call"


def test_answers_render_as_bodies_not_cards():
    prose = AnswerOutcome(body=MarkdownAnswer(text="146 of 161."), verification="verified")
    assert card_for(prose) is None
    assert render_outcome_text(prose) == "146 of 161."

    table = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["supplier", "total", "wait"],
                rows=[{"supplier": "RVX01", "total": 8308.92139244107, "wait": None}],
                total_row_count=1,
                column_formats={"total": ColumnFormat(kind="money", symbol="$")},
            ),
            caption="SELECT ...",
        ),
        verification="unverified",
    )
    text = render_outcome_text(table)
    lines = text.splitlines()
    assert lines[0] == UNVERIFIED_BADGE
    assert "$8,308.92" in lines[3] and "—" in lines[3]
    assert lines[-1] == "(SELECT ...)"


def test_the_page_repeats_the_card_vocabulary_verbatim():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for title in CARD_TITLES.values():
        assert title in script, title
    assert UNVERIFIED_BADGE in script
    # The engineer's diagnosis never reaches a card.
    assert "outcome.detail" not in script.replace("outcome.detail (the engineer's", "")
