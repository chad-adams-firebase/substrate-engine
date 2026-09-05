"""Full scripted turns through the real graph and real tools: answer,
fail-closed exits, the verify-retry-unverified ladder, implausible
evidence, table pass-through, iteration cap."""

from engine.ports.types import LLMResponse
from engine.verifier.models import FeedbackItem, RegenerationFeedback
from tests.harness_support import (
    StubVerifier,
    build_ask_session,
    checkpoint_history,
    refused_result,
    retry_result,
    tool_call,
    unverified_result,
)

STATS_CALL = tool_call(
    "query_univariate_stats", {"table": "invoices", "column": "status"}
)
GIVE_PROSE = tool_call("give_answer", {"shape": "prose"})


def test_answer_path_routes_tools_drafts_and_verifies(tool_pack):
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(
            content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"
        ),
    ]
    session, ports, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("how many invoice rows are there?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"
    assert result.outcome.body.text == "Invoices has 50 rows."
    assert result.tools_used == ["query_univariate_stats"]
    # The verifier saw the resolved text with its injected span.
    (call,) = verifier.calls
    assert call["draft"].kind == "prose"
    assert call["draft"].text == "Invoices has 50 rows."
    assert call["draft"].injected_spans  # the 50 was code-injected


def test_draft_cites_the_clean_invocation_not_the_errored_one(tool_pack):
    """Fix pass 4 (gate verdict N11): the P-N11 shape — e0 errors, the
    answer sits in e1. The drafter's view carries e0 only as a
    collapsed status stub (no error text to anchor on), and the draft
    resolves against e1 cleanly."""
    responses = [
        tool_call("query_univariate_stats", {"nope": 1}),  # e0: errors
        STATS_CALL,  # e1: the clean answer
        GIVE_PROSE,
        LLMResponse(
            content="Invoices has {{e1.rows[0].row_count}} rows.", model="s"
        ),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("how many invoice rows are there?")

    assert result.outcome.kind == "answer"
    assert result.outcome.body.text == "Invoices has 50 rows."

    from engine.config.models import PortName

    stub = ports.get(PortName.LLM)
    sent = stub.calls[-1]["messages"][1].content  # the drafter's evidence
    assert '"status":"error"' in sent  # e0 is visible as a failed call…
    assert "Invalid arguments" not in sent  # …but its error text is not
    assert '"note":"call failed; supports no citations or placeholders"' in sent


def test_clean_day_zero_count_drafts_and_verifies(tool_pack):
    """Fix-pass-4 follow-up (HN-ERRORS): clean-day recent_errors
    evidence carries error_count 0 — an answer, not an absence. The
    drafter's view holds no run_status:null to corroborate an
    emptiness misreading, and a draft citing the 0 verifies."""
    responses = [
        tool_call(
            "check_execution",
            {
                "component": "benchmark_scoring",
                "mode": "recent_errors",
                "window_start": "2026-03-13T00:00:00+00:00",
                "window_end": "2026-03-14T00:00:00+00:00",
            },
        ),
        GIVE_PROSE,
        LLMResponse(
            content=(
                "Benchmark scoring logged {{e0.error_count}} errors in "
                "that window — a clean day."
            ),
            model="s",
        ),
    ]
    session, ports, _ = build_ask_session(
        tool_pack, responses, real_verifier=True
    )
    result = session.ask("did benchmark scoring have any errors that day?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"
    assert "logged 0 errors" in result.outcome.body.text

    from engine.config.models import PortName

    stub = ports.get(PortName.LLM)
    sent = stub.calls[-1]["messages"][1].content  # the drafter's evidence
    assert '"error_count":0' in sent
    assert '"run_status"' not in sent  # the mode's unused half, suppressed


def test_a_verified_shrug_ships_as_a_refusal(tool_pack):
    # Addendum N7, the U6 twin pair: "the evidence does not provide…"
    # passed verification with zero claims and exited 0 while its twin
    # refused with 3. Same substance now gets the same outcome shape:
    # a claim-free insufficiency answer converts to refuse, never 0.
    shrug = (
        "The evidence does not provide reviewer-level assignments, "
        "so this cannot be determined."
    )
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(content=shrug, model="s"),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("who reviews the most invoices?")
    assert result.outcome.kind == "refuse"
    assert result.outcome.reason == shrug

    # The twin that refuses at the router keeps its non-zero exit.
    twin, _, _ = build_ask_session(
        tool_pack,
        [
            tool_call(
                "refuse",
                {
                    "reason": "no reviewer assignment data",
                    "what_would_work": "a per-rule breakdown",
                },
            )
        ],
    )
    assert twin.ask("who reviews the most invoices?").outcome.kind == "refuse"


def test_zero_claim_prose_without_insufficiency_still_answers(tool_pack):
    # Addendum N7's guard is a conjunction: claim-free prose that
    # asserts nothing about missing evidence is a legitimate answer.
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(
            content="This application audits supplier invoices.", model="s"
        ),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("what is this app?")
    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"


def test_refuse_clarify_escalate_are_first_class_exits(tool_pack):
    for name, args, kind, field, expected in [
        (
            "refuse",
            {"reason": "out of scope", "what_would_work": "a data question"},
            "refuse",
            "reason",
            "out of scope",
        ),
        ("clarify", {"question": "which week?"}, "clarify", "question", "which week?"),
        ("escalate", {"reason": "policy decision"}, "escalate", "reason", "policy decision"),
    ]:
        session, _, verifier = build_ask_session(
            tool_pack, [tool_call(name, args)]
        )
        result = session.ask("q")
        assert result.outcome.kind == kind
        assert getattr(result.outcome, field) == expected
        assert verifier.calls == []  # no draft, nothing to verify
        assert result.verdict is None
        assert result.evidence_bundle_ref is None


def test_mismatch_retries_with_feedback_then_ships_unverified(tool_pack):
    feedback = RegenerationFeedback(
        items=[
            FeedbackItem(
                surface="14,600",
                sentence="There were 14,600 rows.",
                kind="numeric",
                nearest_evidence=["50 (count, e0.rows[0].row_count)"],
            )
        ]
    )
    verifier = StubVerifier([retry_result(feedback), unverified_result()])
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(content="There were 14,600 rows.", model="s"),
        LLMResponse(content="There were 14,600 rows, still.", model="s"),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses, verifier=verifier)
    result = session.ask("how many?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "unverified"
    assert [c["attempt"] for c in verifier.calls] == [1, 2]
    # The second drafting call carried the mismatch feedback.
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    redraft_messages = llm.calls[3]["messages"]
    assert any("14,600" in m.content for m in redraft_messages)
    assert any("failed verification" in m.content for m in redraft_messages)
    assert result.verdict.disposition == "unverified"
    assert len(result.verdict.attempts) == 2


def test_implausible_evidence_becomes_a_refusal(tool_pack):
    verifier = StubVerifier([refused_result("COUNT contradicts stats")])
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(content="There are 1,000,000 rows.", model="s"),
    ]
    session, _, _ = build_ask_session(tool_pack, responses, verifier=verifier)
    result = session.ask("how many?")

    assert result.outcome.kind == "refuse"
    # The verdict's diagnosis is detail; the reason names the gate in
    # plain words (Block 2's manager-language cards).
    assert "COUNT contradicts stats" in result.outcome.detail
    assert "COUNT contradicts stats" not in result.outcome.reason
    assert "don't hold up against what the data can support" in result.outcome.reason
    assert result.verdict.disposition == "refused"


def test_table_answers_still_pass_through_the_verifier(tool_pack):
    responses = [
        STATS_CALL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
    ]
    session, _, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("show me the stats")

    assert result.outcome.kind == "answer"
    assert result.outcome.body.kind == "table"
    assert "row_count" in result.outcome.body.table.columns
    (call,) = verifier.calls  # CLAUDE.md: no bypasses, table included
    assert call["draft"].kind == "table_passthrough"


def test_untabular_evidence_index_feeds_back_and_reroutes(tool_pack):
    responses = [
        tool_call("app_primer"),
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
        tool_call("refuse", {"reason": "cannot present that as a table"}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("show the primer as a table")
    assert result.outcome.kind == "refuse"
    # The nudge is a user message after the primer's tool message —
    # valid there, and not a format the model could pattern-complete.
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    nudged = llm.calls[2]["messages"]
    assert nudged[-1].role == "user"
    assert "not table-shaped" in nudged[-1].content
    assert nudged[-2].role == "tool"


def test_iteration_cap_is_a_refuse_outcome_without_an_llm_call(tool_pack):
    # The router keeps asking for tools; the cap must convert to a
    # refusal, and the (cap+1)th router LLM call must never happen.
    responses = [STATS_CALL] * 6
    session, ports, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("loop forever")

    assert result.outcome.kind == "refuse"
    # Block 2: the card speaks plainly; the step count is engineer
    # detail for the CLI and the inspector, never the reason.
    assert "budget" in result.outcome.detail and "6 router steps" in result.outcome.detail
    assert "budget" not in result.outcome.reason and "6" not in result.outcome.reason
    assert result.outcome.what_would_work
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    assert len(llm.calls) == 6  # exactly the scripted budget, not 7
    assert verifier.calls == []


def test_protocol_violation_nudges_then_recovers(tool_pack):
    responses = [
        LLMResponse(content="The answer is probably 42.", model="s"),  # prose
        tool_call("refuse", {"reason": "cannot answer"}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("q")
    assert result.outcome.kind == "refuse"
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    nudge = llm.calls[1]["messages"][-1]
    assert "calling one of" in nudge.content
    # The trail line stays short; what the router wrote rides beside
    # it for the turn log and the inspector (coverage pass, B2).
    (violation,) = [e for e in result.events if e.detail == "protocol violation — nudging"]
    assert violation.raw_response == "The answer is probably 42."
    assert all(e.raw_response is None for e in result.events if e is not violation)


def test_a_text_form_control_verb_is_parsed_and_leaves_a_trace(tool_pack):
    """Polish Pass: the verb written as prose is read as the call —
    no nudge, one router step — and provenance says the channel error
    was tolerated, with what the router wrote beside it."""
    responses = [
        LLMResponse(content='refuse({"reason": "cannot answer"})', model="s"),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("q")
    assert result.outcome.kind == "refuse"
    assert result.outcome.reason == "cannot answer"
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    assert len(llm.calls) == 1  # no nudge round
    (trace,) = [e for e in result.events if e.detail.startswith("text-form")]
    assert trace.detail == "text-form refuse parsed as the call"
    assert trace.raw_response == 'refuse({"reason": "cannot answer"})'
    assert not any("protocol violation" in e.detail for e in result.events)


BAD_DRAFT = LLMResponse(
    content="There are {{e0.rows[0].count_star()}} rows.", model="s"
)


def test_placeholder_exhaustion_falls_back_to_table(tool_pack):
    # Carryback #3b: correct evidence in the bundle, placeholder
    # grammar unable to address it, answer refused. Exhaustion now
    # degrades to the untouched table envelope — still verified.
    responses = [STATS_CALL, GIVE_PROSE, BAD_DRAFT, BAD_DRAFT, BAD_DRAFT]
    session, _, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("how many invoice rows are there?")

    assert result.outcome.kind == "answer"
    assert result.outcome.body.kind == "table"
    assert "row_count" in result.outcome.body.table.columns
    (call,) = verifier.calls  # no bypasses: the fallback is verified
    assert call["draft"].kind == "table_passthrough"
    draft_events = [e for e in result.events if e.node == "draft"]
    assert any(
        "count_star()" in e.detail and "as a table" in e.detail
        for e in draft_events
    )


def test_fallback_prefers_the_referenced_evidence_index(tool_pack):
    # The failed placeholders cite e0; the table ships from e0 even
    # though e1 (gathered later) also projects.
    other_stats = tool_call(
        "query_univariate_stats", {"table": "invoices", "column": "supplier_id"}
    )
    responses = [STATS_CALL, other_stats, GIVE_PROSE] + [BAD_DRAFT] * 3
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("how many?")

    assert result.outcome.kind == "answer"
    assert result.outcome.body.kind == "table"
    columns = {row["column_name"] for row in result.outcome.body.table.rows}
    assert columns == {"status"}  # e0, not the later supplier_id stats


def test_no_projectable_evidence_still_refuses(tool_pack):
    responses = [tool_call("app_primer"), GIVE_PROSE] + [BAD_DRAFT] * 3
    session, _, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("q")

    assert result.outcome.kind == "refuse"
    assert verifier.calls == []
    draft_events = [e for e in result.events if e.node == "draft"]
    assert any("no table-shaped evidence" in e.detail for e in draft_events)


def test_real_verifier_swaps_in_and_verifies_a_clean_turn(tool_pack):
    # The stub proves the seam; this proves the real Verifier fits it:
    # a clean placeholder-drafted answer through real tools verifies.
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(
            content=(
                "The `invoices` table has {{e0.rows[0].row_count}} rows and "
                "{{e0.rows[0].distinct_count}} distinct status values."
            ),
            model="s",
        ),
    ]
    session, _, _ = build_ask_session(tool_pack, responses, real_verifier=True)
    result = session.ask("how big is invoices?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"
    assert result.verdict.disposition == "verified"
    numeric = [
        c
        for a in result.verdict.attempts
        for c in a.claims
        if c.kind == "numeric"
    ]
    assert numeric and all(c.injected for c in numeric)
    # Addendum N3: injected figures are verified by construction, each
    # carrying the evidence path the resolver injected it from.
    assert all(c.status == "matched_injected" for c in numeric)
    assert {c.evidence_ref for c in numeric} == {
        "e0.rows[0].row_count",
        "e0.rows[0].distinct_count",
    }


def test_status_events_reach_the_listener_and_the_result(tool_pack):
    seen = []
    responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(content="Fifty rows.", model="s"),
    ]
    session, _, _ = build_ask_session(
        tool_pack, responses, listener=seen.append
    )
    result = session.ask("q")

    nodes = [e.node for e in result.events]
    assert "route" in nodes
    assert "tool:query_univariate_stats" in nodes
    assert "draft" in nodes and "verify" in nodes and "finalize" in nodes
    assert [e.node for e in seen] == nodes  # one emission, two destinations


# --- Block 2: values, not passages --------------------------------------

READ_SOURCE = tool_call(
    "read_source", {"node": "invoiceguard.spine.rules_engine.rule_rate_variance"}
)
INLINE_PASTE = LLMResponse(
    content="The source is {{e0.text}} which flags a line.", model="s"
)
FENCED_QUOTE = LLMResponse(
    content="The rule, verbatim:\n\n```python\n{{e0.text}}\n```\n", model="s"
)


def test_a_passage_pasted_mid_sentence_is_retried_with_the_rule(tool_pack):
    responses = [READ_SOURCE, GIVE_PROSE, INLINE_PASTE, FENCED_QUOTE]
    session, ports, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("show me the source of rule_rate_variance")

    assert result.outcome.kind == "answer"
    text = result.outcome.body.text
    assert text.startswith("The rule, verbatim:")
    assert "```python\ndef rule_rate_variance(" in text
    # The retry told the drafter exactly what to do with a passage.
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    feedback = llm.calls[-1]["messages"][-1].content
    assert "{{e0.text}}" in feedback and "passage" in feedback
    assert "fenced code block" in feedback
    (call,) = verifier.calls  # the retry never bypassed the Verifier
    assert call["draft"].injected_spans
    draft_events = [e.detail for e in result.events if e.node == "draft"]
    assert any("{{e0.text}}" in d and "retrying (1/2)" in d for d in draft_events)


def test_passages_still_inline_at_exhaustion_ship_as_written(tool_pack):
    """A lumpy seam must not cost the answer: with every placeholder
    resolving and only the placement wrong, the retries run out and
    the passage ships inline — evented, and verified like any prose."""
    responses = [READ_SOURCE, GIVE_PROSE] + [INLINE_PASTE] * 3
    session, _, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("show me the source of rule_rate_variance")

    assert result.outcome.kind == "answer"
    text = result.outcome.body.text
    assert text.startswith("The source is def rule_rate_variance(")
    assert "{{e0.text}}" not in text
    (call,) = verifier.calls
    assert call["draft"].injected_spans
    draft_events = [e.detail for e in result.events if e.node == "draft"]
    assert any("shipping as written" in d and "{{e0.text}}" in d for d in draft_events)


TEXT_PATH = LLMResponse(
    content="The factor is {{e0.text.QUANTITY_SPIKE_FACTOR}}.", model="s"
)


def test_a_path_into_source_text_is_retried_with_the_shape_named(tool_pack):
    """Post-Block-2 W4 rep 4 exhausted its retries on five
    {{e3.text.CONSTANT}} placeholders with feedback that only said "did
    not resolve". The feedback now names the shape."""
    responses = [READ_SOURCE, GIVE_PROSE, TEXT_PATH, FENCED_QUOTE]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("show me the source of rule_rate_variance")
    assert result.outcome.kind == "answer"
    from engine.config.models import PortName

    llm = ports.get(PortName.LLM)
    feedback = llm.calls[-1]["messages"][-1].content
    assert "{{e0.text.QUANTITY_SPIKE_FACTOR}}" in feedback
    assert "paths into a text passage" in feedback
    assert "placeholders never reach inside text" in feedback


def test_question_of_turn_reads_both_history_layouts():
    """The backfill verb's reading of the checkpoint history: today's
    records by their own turn number, and the pre-Block-4 (user,
    assistant) pairs by pair index, through one upgrade."""
    from engine.harness.graph import question_of_turn
    from engine.harness.state import HistoryTurn
    from engine.ports.types import Message

    legacy = [
        Message(role="user", content="How many invoices?"),
        Message(role="assistant", content="[table: result set]"),
        Message(role="user", content="And per supplier?"),
        Message(role="assistant", content="[refused: no]"),
    ]
    records = [
        HistoryTurn(turn=1, question="How many invoices?", answer="[table: result set]", kind="table"),
        HistoryTurn(turn=3, question="And per supplier?", answer="[refused: no]", kind="refuse"),
    ]
    for history in (legacy, records):
        assert question_of_turn(history, 1) == "How many invoices?"
        assert question_of_turn(history, 0) is None
        assert question_of_turn(history, 9) is None
    assert question_of_turn(legacy, 2) == "And per supplier?"
    assert question_of_turn(records, 2) is None  # turn 2 raised; no record
    assert question_of_turn(records, 3) == "And per supplier?"
    assert question_of_turn([], 1) is None


# --- Close Pass: the loop transcript is native tool messages ------------


def test_the_second_router_call_sees_native_tool_history(tool_pack):
    """B2 fabricated a "Tool results:" block and wrote give_answer as
    text under a "Requested:" echo — completions of the old prose
    rendering. The router now sees its own call as an assistant
    tool_calls message and the result as the tool message answering
    it, so there is no text format to complete."""
    import json

    from engine.config.models import PortName

    responses = [STATS_CALL, tool_call("refuse", {"reason": "enough"})]
    session, ports, _ = build_ask_session(tool_pack, responses)
    session.ask("how many invoice rows are there?")

    llm = ports.get(PortName.LLM)
    m = llm.calls[1]["messages"]
    assert m[-2].role == "assistant"
    assert m[-2].tool_calls[0].name == "query_univariate_stats"
    assert m[-1].role == "tool"
    assert m[-1].tool_call_id == m[-2].tool_calls[0].id
    assert json.loads(m[-1].content)["evidence_index"] == 0
    assert not any("Tool results:" in msg.content for msg in m)
    assert not any(msg.content.startswith("Requested:") for msg in m)


def test_a_hallucinated_tool_gets_exactly_one_tool_message(tool_pack):
    """The transcript invariant holds for a name the registry does not
    know: the call still gets its one tool message — the note naming
    what exists — and nothing joins the evidence."""
    from engine.config.models import PortName

    responses = [
        tool_call("query_the_database"),
        tool_call("refuse", {"reason": "no such tool"}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("q")

    llm = ports.get(PortName.LLM)
    m = llm.calls[1]["messages"]
    assert m[-1].role == "tool"
    assert m[-1].tool_call_id == m[-2].tool_calls[0].id
    assert "query_the_database" in m[-1].content
    assert "run_sql" in m[-1].content
    assert result.tools_used == []


# --- Close Pass: a table answer names its reading -----------------------

RUN_SQL_ON_THE_METRIC = tool_call(
    "run_sql", {"question": "What is the flagged share of invoices?"}
)
COUNT_SQL = LLMResponse(
    content="```sql\nSELECT COUNT(*) AS n FROM invoices\n```", model="scripted"
)


def test_a_table_answer_over_a_metric_with_readings_names_its_reading(tool_pack):
    """The result lists the metric's readings; a declared name rides to
    the answer as a typed field, and the Verifier still sees the
    caption as the verbatim SQL — no sentence to re-verify."""
    responses = [
        RUN_SQL_ON_THE_METRIC,
        COUNT_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "reading": "substantive"}),
    ]
    session, ports, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("What is the flagged share of invoices?")
    assert result.outcome.kind == "answer" and result.outcome.body.kind == "table"
    assert result.outcome.body.reading == "substantive"
    assert result.outcome.body.caption == "SELECT COUNT(*) AS n FROM invoices"
    (call,) = verifier.calls
    assert call["draft"].text == "SELECT COUNT(*) AS n FROM invoices"
    # The router saw the readings, names and meanings, in the tool result.
    from engine.config.models import PortName

    tool_message = ports.get(PortName.LLM).calls[2]["messages"][-1]
    assert tool_message.role == "tool"
    assert '"readings":[{"meaning":"any finding row counts, bookkeeping included.","name":"all findings"}' in tool_message.content


def test_an_undeclared_reading_is_nudged_with_the_valid_set(tool_pack):
    responses = [
        RUN_SQL_ON_THE_METRIC,
        COUNT_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "reading": "gross"}),
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "reading": "all findings"}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("What is the flagged share of invoices?")
    assert result.outcome.body.reading == "all findings"
    details = [event.detail for event in result.events]
    assert "protocol violation — reading not declared — nudging" in details
    from engine.config.models import PortName

    nudge = ports.get(PortName.LLM).calls[3]["messages"][-1]
    assert nudge.role == "user"
    assert "reading 'gross' is not one this result lists" in nudge.content
    assert "'all findings', 'substantive'" in nudge.content


def test_a_missing_reading_is_accepted_and_an_unneeded_one_is_dropped(tool_pack):
    """Lenient on missing (phrase matching over-reaches, and a forced
    reading would be a wrong sentence on a right table); a reading
    given where nothing is declared is dropped, not nudged."""
    responses = [
        RUN_SQL_ON_THE_METRIC,
        COUNT_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("What is the flagged share of invoices?")
    assert result.outcome.body.reading == ""
    assert all("nudging" not in event.detail for event in result.events)

    responses = [
        STATS_CALL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "reading": "substantive"}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("show me the stats")
    assert result.outcome.body.kind == "table" and result.outcome.body.reading == ""
    assert all("nudging" not in event.detail for event in result.events)


# --- Backlog Pass: a follow-up says what it is about, and the history
# --- keeps what a turn established ------------------------------------

TOP_RULE_CALL = tool_call("run_sql", {"question": "Which rule fires most often?"})
TOP_RULE_SQL = LLMResponse(
    content=(
        "```sql\nSELECT f.rule_name AS rule_name, COUNT(*) AS fire_count FROM findings f "
        "GROUP BY f.rule_name ORDER BY fire_count DESC, f.rule_name LIMIT 1\n```"
    ),
    model="scripted",
)


def test_a_table_turn_establishes_its_entity_and_the_transcript_names_it(tool_pack):
    """Turn 6, replayed on the snapshot: the one-row rule table leaves
    `About: rule <name>.` on the history line the router reads next
    turn, and the anchor on the checkpoint for the Verifier."""
    responses = [
        TOP_RULE_CALL,
        TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("Which rule fires most often?")
    assert result.outcome.kind == "answer" and result.outcome.body.kind == "table"
    (row,) = result.outcome.body.table.rows
    name = row["rule_name"]
    history = checkpoint_history(session, result.conversation_id)
    (record,) = history
    assert record.answer.startswith(f"[table: About: rule {name}. SELECT f.rule_name")
    (anchor,) = record.anchors.entities
    assert (anchor.kind, anchor.column, anchor.value, anchor.source) == (
        "rule", "findings.rule_name", name, "cell"
    )
    assert record.anchors.turn == 1 and record.anchors.keys == []


def test_a_count_turn_establishes_nothing_and_its_transcript_is_unchanged(tool_pack):
    responses = [
        STATS_CALL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("show me the stats")
    (record,) = checkpoint_history(session, result.conversation_id)
    assert record.answer.startswith("[table: ")
    assert "About:" not in record.answer
    assert record.anchors.entities == []


def test_a_declared_about_rides_to_both_shapes_and_the_checkpoint(tool_pack):
    responses = [
        TOP_RULE_CALL,
        TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": "line_note"}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("Tell me more about that rule.")
    assert result.outcome.body.about == "line_note"
    (record,) = checkpoint_history(session, result.conversation_id)
    declared = [a for a in record.anchors.entities if a.source == "declared"]
    assert declared == [type(declared[0])(kind="rule", column="", value="line_note", source="declared")]

    responses = [
        TOP_RULE_CALL,
        TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "prose", "about": "line_note"}),
        LLMResponse(content="The rule that fires most is `{{e0.table.rows[0].rule_name}}`.", model="scripted"),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("Tell me more about that rule.")
    assert result.outcome.body.kind == "markdown" and result.outcome.body.about == "line_note"


def test_the_next_turns_tools_see_what_the_conversation_established(tool_pack):
    """The context reaches run_sql: the second turn's SQL author is told
    the key turn 1 carried, and the user's words ground the key lint."""
    from engine.config.models import PortName

    responses = [
        TOP_RULE_CALL,
        TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
        tool_call("run_sql", {"question": "How many findings has that rule produced?"}),
        LLMResponse(
            content="```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = 'line_note'\n```",
            model="scripted",
        ),
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": "line_note"}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    first = session.ask("Which rule fires most often?")
    second = session.ask("How many findings has that rule produced?", conversation_id=first.conversation_id)
    assert second.outcome.kind == "answer", second.outcome
    calls = ports.get(PortName.LLM).calls
    grounding = calls[4]["messages"][0].content  # the second turn's SQL author
    name = first.outcome.body.table.rows[0]["rule_name"]
    # A rule's name is the key a follow-up filters on: the grounding
    # states it, verbatim, with the turn that established it.
    assert "## Keys this conversation established" in grounding
    assert f"findings.rule_name = '{name}' (rule, turn 1)" in grounding
    # And the anchor rode into the router's history line.
    router_messages = calls[3]["messages"]
    assert any(m.role == "assistant" and m.content.startswith(f"[table: About: rule {name}. ") for m in router_messages)


def test_the_verify_node_hands_the_verifier_the_history_and_the_declaration(tool_pack):
    """Backlog Pass: the anchor check reads every prior turn's anchors
    (the full history, not the router's window) and the router's about."""
    responses = [
        TOP_RULE_CALL,
        TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
        tool_call("run_sql", {"question": "How many findings has that rule produced?"}),
        LLMResponse(
            content="```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = 'line_note'\n```",
            model="scripted",
        ),
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": "line_note"}),
    ]
    session, _, verifier = build_ask_session(tool_pack, responses)
    first = session.ask("Which rule fires most often?")
    session.ask("Tell me more about that rule.", conversation_id=first.conversation_id)
    name = first.outcome.body.table.rows[0]["rule_name"]
    first_call, second_call = verifier.calls
    assert first_call["context"].prior == [] and first_call["context"].about is None
    (prior,) = second_call["context"].prior
    assert prior.turn == 1 and prior.entities[0].value == name
    assert second_call["context"].about == "line_note"


# --- Fix Pass: a warned turn establishes nothing, and only an answer
# --- establishes an entity ---------------------------------------------

ANCHOR_DETAIL = (
    "the question refers to that rule; turn 1's evidence established "
    "`line_note`, and this answer filters on `findings.rule_name = 'new_supplier'`"
)
DRIFT_CALL = tool_call("run_sql", {"question": "How many findings has the rule 'new_supplier' produced?"})
DRIFT_SQL = LLMResponse(
    content="```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = 'new_supplier'\n```",
    model="scripted",
)


def anchor_warn_result():
    from engine.verifier.models import AttemptRecord, PlausibilityRecord, VerifierResult

    return VerifierResult(
        disposition="unverified",
        attempt_record=AttemptRecord(attempt=1, claims=[], unmatched_count=0),
        plausibility=[PlausibilityRecord(check="anchor.entity_mismatch", severity="warn", detail=ANCHOR_DETAIL)],
    )


def test_a_warned_turn_writes_no_anchors_and_its_transcript_carries_the_correction(tool_pack):
    """MT-ANCHOR rep 4, the mechanism: turn 2's drift was warned and
    still became turn 3's anchor. Now the warned turn's record keeps
    no entity, the prior anchor survives on the history, the next
    turn's router reads the correction in place of the About line, and
    the next turn's SQL author is still told turn 1's key."""
    from engine.config.models import PortName
    from tests.harness_support import verified_result

    responses = [
        TOP_RULE_CALL, TOP_RULE_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
        DRIFT_CALL, DRIFT_SQL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": "new_supplier"}),
        tool_call("run_sql", {"question": "How many findings has it produced?"}),
        LLMResponse(content="```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = 'line_note'\n```", model="scripted"),
        tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": "line_note"}),
    ]
    verifier = StubVerifier([verified_result(), anchor_warn_result(), verified_result()])
    session, ports, _ = build_ask_session(tool_pack, responses, verifier=verifier)
    first = session.ask("Which rule fires most often?")
    name = first.outcome.body.table.rows[0]["rule_name"]
    second = session.ask("How many findings has that rule produced?", conversation_id=first.conversation_id)
    assert second.outcome.kind == "answer" and second.outcome.verification == "unverified"
    third = session.ask("How many findings has it produced?", conversation_id=first.conversation_id)
    assert third.outcome.kind == "answer"

    one, two, three = checkpoint_history(session, first.conversation_id)
    assert [a.value for a in one.anchors.entities] == [name]
    assert two.anchors.entities == [] and two.anchors.contradicted_kind == "rule"
    assert two.anchors.contradiction == ANCHOR_DETAIL
    assert two.answer.startswith(f"[table: Unverified: {ANCHOR_DETAIL}. SELECT COUNT(*)")
    assert "About: new_supplier" not in two.answer
    assert three.anchors.contradicted_kind == ""
    assert [a.value for a in three.anchors.entities if a.column] == ["line_note"]

    calls = ports.get(PortName.LLM).calls
    router_messages = calls[6]["messages"]  # turn 3's first router step
    assert any(m.role == "assistant" and m.content.startswith("[table: Unverified: ") for m in router_messages)
    assert not any("About: new_supplier" in m.content for m in router_messages)
    grounding = calls[7]["messages"][0].content  # turn 3's SQL author
    assert f"findings.rule_name = '{name}' (rule, turn 1)" in grounding
    assert "new_supplier" not in grounding
    # The Verifier at turn 3 saw the warned record, entity-free.
    prior = verifier.calls[2]["context"].prior
    assert [(p.turn, len(p.entities), p.contradicted_kind) for p in prior] == [(1, 1, ""), (2, 0, "rule")]


def test_a_refusal_after_a_one_row_query_keeps_its_keys_and_establishes_nothing(tool_pack):
    top_invoice = LLMResponse(
        content="```sql\nSELECT i.id AS invoice_id, i.invoice_number AS invoice_number FROM invoices i ORDER BY i.invoice_total DESC LIMIT 1\n```",
        model="scripted",
    )
    responses = [
        tool_call("run_sql", {"question": "Which invoice has the highest total?"}), top_invoice,
        tool_call("refuse", {"reason": "The totals column is not exposed.", "what_would_work": "asking for the count"}),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("Which invoice has the highest total?")
    assert result.outcome.kind == "refuse"
    (record,) = checkpoint_history(session, result.conversation_id)
    assert record.anchors.entities == []
    assert {k.column for k in record.anchors.keys} == {"invoices.id", "invoices.invoice_number"}
    assert record.answer.startswith("[refused: ")


def test_with_the_real_verifier_a_drift_after_a_warning_ships_unverified_never_verified(tool_pack):
    """The breach, end to end: turn 1 the top rule (line_note in the
    pack; the snapshot world names its own); turn 2 "that rule" counts
    another rule and is warned; turn 3 "it" counts that other rule
    again — the window reads it against the anchor, [UNVERIFIED], exit
    2 — where the post-backlog run verified 197 at exit 0. The twin
    that reads the correction and counts the anchor verifies."""
    from engine.eval.world import World

    (top,) = World.from_pack(tool_pack).sql(
        "SELECT rule_name FROM findings GROUP BY rule_name ORDER BY COUNT(*) DESC, rule_name LIMIT 1"
    )
    anchor = top["rule_name"]
    other = "new_supplier" if anchor != "new_supplier" else "line_note"

    def responses(third_rule: str):
        return [
            TOP_RULE_CALL, TOP_RULE_SQL,
            tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
            tool_call("run_sql", {"question": f"How many findings has the rule '{other}' produced?"}),
            LLMResponse(content=f"```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = '{other}'\n```", model="scripted"),
            tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
            tool_call("run_sql", {"question": "How many findings has it produced?"}),
            LLMResponse(content=f"```sql\nSELECT COUNT(*) AS n FROM findings f WHERE f.rule_name = '{third_rule}'\n```", model="scripted"),
            tool_call("give_answer", {"shape": "table", "evidence_index": 0, "about": third_rule}),
        ]

    for third_rule, expected in ((other, "unverified"), (anchor, "verified")):
        session, _, _ = build_ask_session(tool_pack, responses(third_rule), real_verifier=True)
        first = session.ask("Which rule fires most often?")
        second = session.ask("How many findings has that rule produced?", conversation_id=first.conversation_id)
        assert second.outcome.verification == "unverified"
        assert any(p.check == "anchor.entity_mismatch" for p in second.verdict.plausibility)
        third = session.ask("How many findings has it produced?", conversation_id=first.conversation_id)
        assert third.outcome.kind == "answer" and third.outcome.verification == expected, third_rule
        anchor_findings = [p for p in third.verdict.plausibility if p.check == "anchor.entity_mismatch"]
        if expected == "unverified":
            (finding,) = anchor_findings
            assert finding.detail.startswith("the question's pronoun follows turn 2's anchor warning")
        else:
            assert anchor_findings == []
