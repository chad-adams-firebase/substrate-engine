"""Context management end to end (Brief §10.3, Phase 5 Block 4): the
summarize node's gate, its input, the regenerate-once-then-scrub, the
router's view (summary section plus the verbatim window), a failure
that keeps the old summary, and a 30-turn conversation whose last turn
still anchors on its first."""

import yaml

from engine.config.models import ContextSettings, PortName
from engine.ports.types import LLMResponse
from tests.harness_support import build_ask_session, tool_call

STATS_CALL = tool_call(
    "query_univariate_stats", {"table": "invoices", "column": "status"}
)
GIVE_PROSE = tool_call("give_answer", {"shape": "prose"})
GIVE_TABLE = tool_call("give_answer", {"shape": "table", "evidence_index": 0})
ROWS_PROSE = LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s")
ANSWER_TURN = [STATS_CALL, GIVE_PROSE, ROWS_PROSE]


def _refuse(reason="no"):
    return tool_call("refuse", {"reason": reason})


def _say(text):
    return LLMResponse(content=text, model="s")


def _pack(tool_pack, tmp_path, **context):
    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["adapters"]["work_store"]["settings"]["database"] = str(tmp_path / "work.db")
    config["harness"] = {"context": context}
    config_path.write_text(yaml.safe_dump(config))
    return tool_pack


def _run(pack, responses, questions):
    session, ports, _ = build_ask_session(pack, responses)
    results = []
    conversation_id = None
    for question in questions:
        result = session.ask(question, conversation_id=conversation_id)
        conversation_id = result.conversation_id
        results.append(result)
    return results, ports.get(PortName.LLM), session


def _summarizer_calls(llm):
    return [c for c in llm.calls if c["tools"] is None and "Turns to fold in:" in c["messages"][-1].content or (c["tools"] is None and "Your summary broke the rules" in c["messages"][-1].content)]


def test_summary_is_refreshed_regenerated_once_then_scrubbed_and_seen_by_the_router(
    tool_pack, tmp_path
):
    pack = _pack(tool_pack, tmp_path, last_n_turns=1, summary_refresh_after_turns=1)
    responses = [
        *ANSWER_TURN,
        _refuse(),
        # Turn 2's fold of turn 1: the reply restates the figure twice.
        _say("In turn 1 the user asked how many invoice rows there are; the assistant said 50 rows."),
        _say("In turn 1 the user asked for the invoice row count; the assistant said 50 rows."),
        _refuse(),
        # Turn 3's fold of turn 2: clean first time.
        _say("Turn 1 established the invoice row count (see turn 1); in turn 2 the user asked what they asked and was refused."),
    ]
    results, llm, _ = _run(
        pack, responses, ["how many invoice rows?", "what did I ask?", "and now?"]
    )
    first, second, third = results
    assert first.outcome.body.text == "Invoices has 50 rows."
    assert first.summary == "" and first.summary_through_turn == 0
    assert "summarize" not in [e.node for e in first.events]  # gate off at turn 1

    # Turn 2: one fold, one regeneration, then the scrub.
    assert second.outcome.kind == "refuse"
    assert second.summary_through_turn == 1
    assert "50" not in second.summary and "turn 1" in second.summary
    assert second.summary == (
        "In turn 1 the user asked for the invoice row count; the assistant said "
        "(see turn 1) rows."
    )
    details = [e.detail for e in second.events if e.node == "summarize"]
    assert details == [
        "Updating conversation summary…",
        "summary updated through turn 1; 1 scrubbed",
    ]
    assert [e.detail for e in second.events if e.node == "finalize"] == ["refuse"]
    fold, regen = llm.calls[4], llm.calls[5]
    for call in (fold, regen):
        assert call["temperature"] == 0.0 and call["tools"] is None
    assert "Turn 1 — user: how many invoice rows?" in fold["messages"][-1].content
    assert "Turn 1 — assistant: Invoices has 50 rows." in fold["messages"][-1].content
    assert "Previous summary: (none yet)" in fold["messages"][-1].content
    feedback = regen["messages"][-1].content
    assert "restates these figures" in feedback and "50" in feedback
    assert regen["messages"][-2].role == "assistant"

    # Turn 3's router: the summary rides in the system message; turn 1
    # is outside the window, turn 2 inside it.
    router = llm.calls[6]
    system = router["messages"][0]
    assert system.role == "system"
    assert "Conversation summary through turn 1" in system.content
    assert "(see turn 1)" in system.content and "50" not in system.content
    roles = [m.role for m in router["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert [m.content for m in router["messages"][1:]] == [
        "what did I ask?", "[refused: no]", "and now?"
    ]
    assert "how many invoice rows?" not in [m.content for m in router["messages"]]
    assert third.summary_through_turn == 2
    assert "Previous summary (through turn 1)" in llm.calls[7]["messages"][-1].content


def test_a_clean_reply_needs_no_regeneration(tool_pack, tmp_path):
    pack = _pack(tool_pack, tmp_path, last_n_turns=1, summary_refresh_after_turns=1)
    responses = [
        *ANSWER_TURN,
        _refuse(),
        _say("In turn 1 the user asked for the invoice row count (see turn 1)."),
    ]
    results, llm, _ = _run(pack, responses, ["how many invoice rows?", "again?"])
    assert len(llm.calls) == 5  # router, drafter, router, router, summarizer
    assert results[1].summary == "In turn 1 the user asked for the invoice row count (see turn 1)."
    assert [e.detail for e in results[1].events if e.node == "summarize"] == [
        "Updating conversation summary…", "summary updated through turn 1",
    ]


def test_a_failed_refresh_keeps_the_previous_summary_and_the_outcome(
    tool_pack, tmp_path
):
    pack = _pack(tool_pack, tmp_path, last_n_turns=1, summary_refresh_after_turns=1)
    responses = [
        *ANSWER_TURN,
        _refuse("second"),
        _say("Turn 1 asked for the row count (see turn 1)."),
        _refuse("third"),
        _say(""),  # an empty reply is a failure, not an empty summary
        _refuse("fourth"),
        # nothing scripted: the summarizer call raises (script exhausted)
    ]
    results, llm, _ = _run(pack, responses, ["rows?", "two", "three", "four"])
    _, second, third, fourth = results
    assert third.outcome.kind == "refuse" and third.outcome.reason == "third"
    assert third.summary == second.summary and third.summary_through_turn == 1
    assert [e.detail for e in third.events if e.node == "summarize"] == [
        "Updating conversation summary…",
        "summary refresh failed: ValueError: empty reply — previous summary kept",
    ]
    assert fourth.outcome.kind == "refuse" and fourth.outcome.reason == "fourth"
    assert fourth.summary == second.summary and fourth.summary_through_turn == 1
    (failed,) = [
        e.detail for e in fourth.events
        if e.node == "summarize" and e.phase == "finish"
    ]
    assert failed.startswith("summary refresh failed: AssertionError: ScriptedLLM exhausted")
    assert failed.endswith("— previous summary kept")
    assert [e.detail for e in fourth.events if e.node == "finalize"] == ["refuse"]


def test_a_table_turn_contributes_its_caption_and_no_figures(tool_pack, tmp_path):
    pack = _pack(tool_pack, tmp_path, last_n_turns=1, summary_refresh_after_turns=1)
    responses = [
        STATS_CALL,
        GIVE_TABLE,
        _refuse(),
        _say("In turn 1 the user asked for the status distribution and got a table (see turn 1)."),
    ]
    results, llm, _ = _run(pack, responses, ["status distribution as a table", "ok"])
    table = results[0].outcome.body
    assert table.kind == "table" and table.table.rows[0]["row_count"] == 50
    fold = llm.calls[3]["messages"][-1].content
    assert "Turn 1 — assistant: [table: result set]" in fold
    assert "50" not in fold
    from engine.harness.state import HistoryTurn
    from engine.harness.summary import figure_set

    assert figure_set([
        HistoryTurn(turn=1, question="status distribution as a table",
                    answer="[table: result set]", kind="table")
    ]) == {}


def test_the_gate_is_the_packs_unless_the_call_overrides_it(tool_pack, tmp_path):
    pack = _pack(tool_pack, tmp_path)  # defaults: 10 / 5
    session, ports, _ = build_ask_session(
        pack,
        [*ANSWER_TURN, _refuse(), _refuse(), _say("Turn 1 asked for rows (see turn 1).")],
    )
    first = session.ask("rows?")
    second = session.ask("two", conversation_id=first.conversation_id)
    assert second.summary == "" and "summarize" not in [e.node for e in second.events]
    override = ContextSettings(last_n_turns=1, summary_refresh_after_turns=1)
    third = session.ask("three", conversation_id=first.conversation_id, context=override)
    assert third.summary_through_turn == 2
    assert third.summary == "Turn 1 asked for rows (see turn 1)."
    assert session.context_of(first.conversation_id).summary == third.summary
    assert session.context_of(first.conversation_id).summary_through_turn == 2
    assert session.context_of(999).summary == ""


def test_a_thirty_turn_conversation_still_anchors_on_its_first_turn(
    tool_pack, tmp_path
):
    """last_n_turns 3, refresh every 2: folds at turns 5, 7, …, 29 —
    thirteen refreshes, the last covering through turn 26. Turn 30's
    router sees that summary naming the supplier from turn 1 without
    its figure, and turns 27–29 verbatim."""
    pack = _pack(tool_pack, tmp_path, last_n_turns=3, summary_refresh_after_turns=2)
    supplier_turn = [
        STATS_CALL,
        GIVE_PROSE,
        _say("Ravenswood Extrusion tops the list with {{e0.rows[0].row_count}} invoices."),
    ]
    responses = list(supplier_turn)
    questions = ["Which supplier sends the most invoices?"]
    fold_turns = [t for t in range(2, 31) if (t - 3) % 2 == 0 and t - 3 >= 2]
    for turn in range(2, 31):
        questions.append(
            "What about that supplier's total?" if turn == 30 else f"unrelated question {turn}"
        )
        responses.append(_refuse(f"r{turn}"))
        if turn in fold_turns:
            through = turn - 3
            responses.append(
                _say(
                    f"In turn 1 the user asked which supplier sends the most invoices; "
                    f"the assistant named Ravenswood Extrusion (see turn 1). Turns 2 "
                    f"through {through} were unrelated questions, all refused."
                )
            )
    results, llm, _ = _run(pack, responses, questions)
    assert fold_turns == [5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29]
    assert results[0].outcome.body.text == "Ravenswood Extrusion tops the list with 50 invoices."
    last = results[-1]
    assert last.turn == 30 and last.summary_through_turn == 26
    assert "Ravenswood Extrusion" in last.summary and "50" not in last.summary
    assert [r.summary_through_turn for r in results[4:8]] == [2, 2, 4, 4]

    router = llm.calls[-1]
    system = router["messages"][0].content
    assert "Conversation summary through turn 26" in system
    assert "Ravenswood Extrusion (see turn 1)" in system and "50" not in system
    window = [m.content for m in router["messages"][1:]]
    assert window == [
        "unrelated question 27", "[refused: r27]",
        "unrelated question 28", "[refused: r28]",
        "unrelated question 29", "[refused: r29]",
        "What about that supplier's total?",
    ]
    # The last fold carried the previous summary forward.
    last_fold = [c for c in llm.calls if "Turns to fold in:" in c["messages"][-1].content][-1]
    assert "Previous summary (through turn 24)" in last_fold["messages"][-1].content
    assert "Turn 26 — user: unrelated question 26" in last_fold["messages"][-1].content


def test_a_table_turn_that_named_its_reading_says_so_in_the_transcript():
    from engine.harness.outcomes import AnswerOutcome, TableAnswer
    from engine.harness.state import transcript_text
    from engine.tools.envelope import Table

    table = Table(columns=["n"], rows=[{"n": 1}], total_row_count=1)
    plain = AnswerOutcome(body=TableAnswer(table=table, caption="SELECT 1"), verification="verified")
    named = AnswerOutcome(
        body=TableAnswer(table=table, caption="SELECT 1", reading="substantive"),
        verification="verified",
    )
    assert transcript_text(plain) == "[table: SELECT 1]"
    assert transcript_text(named) == "[table: Reading: substantive. SELECT 1]"


def test_a_table_turn_names_what_its_evidence_established_in_the_transcript():
    """Backlog Pass: turn 6's line was `[table: SELECT f.rule_name …]`
    and the router could not resolve "that rule" from it. The anchors
    lead the line, before the reading; a declared about stands in when
    the evidence established nothing; prose is unchanged."""
    from engine.harness.outcomes import AnswerOutcome, MarkdownAnswer, TableAnswer
    from engine.harness.state import anchors_text, transcript_text
    from engine.tools.envelope import Anchor, Table, TurnAnchors

    table = Table(columns=["rule_name", "fire_count"], rows=[{"rule_name": "line_note", "fire_count": 505}], total_row_count=1)
    outcome = AnswerOutcome(body=TableAnswer(table=table, caption="SELECT 1"), verification="verified")
    anchors = TurnAnchors(turn=6, entities=[
        Anchor(kind="rule", column="findings.rule_name", value="line_note", source="cell"),
    ])
    assert anchors_text(anchors) == "About: rule line_note."
    assert transcript_text(outcome, anchors) == "[table: About: rule line_note. SELECT 1]"
    assert transcript_text(outcome) == "[table: SELECT 1]"
    two = TurnAnchors(turn=22, entities=[
        Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
        Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
        Anchor(kind="rule", column="findings.rule_name", value="correction_ignored", source="filter"),
        Anchor(kind="rule", column="", value="ignored", source="declared"),
    ])
    assert anchors_text(two) == "About: supplier RVX01 / Ravenswood Extrusion, rule correction_ignored."
    named = AnswerOutcome(
        body=TableAnswer(table=table, caption="SELECT 1", reading="substantive", about="line_note"),
        verification="verified",
    )
    assert transcript_text(named, anchors) == "[table: About: rule line_note. Reading: substantive. SELECT 1]"
    assert transcript_text(named, TurnAnchors()) == "[table: About: line_note. Reading: substantive. SELECT 1]"
    prose = AnswerOutcome(body=MarkdownAnswer(text="It checks notes.", about="line_note"), verification="verified")
    assert transcript_text(prose, anchors) == "It checks notes."


def test_a_warned_turns_transcript_carries_the_contradiction_not_the_drift():
    """Fix Pass R1(a): the Verifier's detail takes the About sentence's
    slot on a table and leads a prose entry, so the router reads the
    correction; the declared About — the drift — is never rendered.
    Non-answers are unchanged."""
    from engine.harness.outcomes import AnswerOutcome, MarkdownAnswer, RefuseOutcome, TableAnswer
    from engine.harness.state import transcript_text
    from engine.tools.envelope import Table, TurnAnchors

    detail = "the question refers to that rule; turn 1's evidence established `line_note`, and this answer never names it"
    warned = TurnAnchors(turn=2, contradicted_kind="rule", contradiction=detail)
    table = Table(columns=["n"], rows=[{"n": 197}], total_row_count=1)
    counted = AnswerOutcome(
        body=TableAnswer(table=table, caption="SELECT 1", reading="substantive", about="new_supplier"),
        verification="unverified",
    )
    assert transcript_text(counted, warned) == f"[table: Unverified: {detail}. Reading: substantive. SELECT 1]"
    assert "new_supplier" not in transcript_text(counted, warned)
    prose = AnswerOutcome(body=MarkdownAnswer(text="The rule `new_supplier` flags new suppliers.", about="new_supplier"),
                          verification="unverified")
    assert transcript_text(prose, warned) == f"[unverified: {detail}] The rule `new_supplier` flags new suppliers."
    refused = RefuseOutcome(reason="No CKG node for that rule.")
    assert transcript_text(refused, warned) == "[refused: No CKG node for that rule.]"
    # An unwarned turn is byte-identical to before.
    assert transcript_text(counted, TurnAnchors(turn=2)) == "[table: About: new_supplier. Reading: substantive. SELECT 1]"
