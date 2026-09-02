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
