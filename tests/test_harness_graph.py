"""Full scripted turns through the real graph and real tools: answer,
fail-closed exits, the verify-retry-unverified ladder, implausible
evidence, table pass-through, iteration cap."""

from engine.ports.types import LLMResponse
from engine.verifier.models import FeedbackItem, RegenerationFeedback
from tests.harness_support import (
    StubVerifier,
    build_ask_session,
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
    assert "COUNT contradicts stats" in result.outcome.reason
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
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("show the primer as a table")
    assert result.outcome.kind == "refuse"


def test_iteration_cap_is_a_refuse_outcome_without_an_llm_call(tool_pack):
    # The router keeps asking for tools; the cap must convert to a
    # refusal, and the (cap+1)th router LLM call must never happen.
    responses = [STATS_CALL] * 6
    session, ports, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("loop forever")

    assert result.outcome.kind == "refuse"
    assert "budget" in result.outcome.reason
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
    assert all(c.status == "matched_exact" for c in numeric)


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
