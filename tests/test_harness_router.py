"""Router-loop mechanics against the real tool registry: execution
order, hallucinated-name absorption, feedback shape."""

import json

from engine.harness.events import EventLog
from engine.harness.router import (
    assistant_echo,
    build_router_messages,
    execute_selections,
    results_message,
    summarize_invocation,
)
from engine.harness.state import ToolSelection
from engine.ports.types import LLMResponse, Message
from tests.conftest import build_tool_registry


def test_selections_execute_in_order_and_emit_events(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    events = EventLog()
    invocations, unknown = execute_selections(
        registry,
        [
            ToolSelection(name="app_primer", arguments={}),
            ToolSelection(
                name="query_univariate_stats",
                arguments={"table": "invoices", "column": "status"},
            ),
        ],
        evidence_so_far=0,
        events=events,
    )
    assert unknown == []
    assert [inv.tool.value for inv in invocations] == [
        "app_primer",
        "query_univariate_stats",
    ]
    assert all(inv.status == "ok" for inv in invocations)
    assert [e.node for e in events.events] == [
        "tool:app_primer",
        "tool:app_primer",
        "tool:query_univariate_stats",
        "tool:query_univariate_stats",
    ]
    assert "evidence[1] ok" in events.events[-1].detail


def test_hallucinated_tool_name_becomes_feedback_not_evidence(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    events = EventLog()
    invocations, unknown = execute_selections(
        registry,
        [ToolSelection(name="query_the_database", arguments={})],
        evidence_so_far=0,
        events=events,
    )
    assert invocations == []
    assert len(unknown) == 1
    assert "query_the_database" in unknown[0]
    assert "run_sql" in unknown[0]  # names what exists


def test_bad_arguments_still_produce_an_error_envelope(tool_pack):
    # Bad args are an LLM mistake the registry already absorbs: the
    # invocation comes back status="error" and joins the evidence, so
    # the router sees exactly what went wrong.
    registry, _ = build_tool_registry(tool_pack)
    invocations, unknown = execute_selections(
        registry,
        [ToolSelection(name="query_univariate_stats", arguments={"nope": 1})],
        evidence_so_far=0,
        events=EventLog(),
    )
    assert unknown == []
    assert invocations[0].status == "error"


def test_summaries_truncate_tables_visibly(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "lookup_data_dictionary", {"table": "invoices"}
    )
    summary = summarize_invocation(invocation, evidence_index=3, max_rows=30)
    assert summary["evidence_index"] == 3
    assert summary["status"] == "ok"
    assert "evidence" not in summary  # residue never reaches the router

    from engine.tools.envelope import RunSqlOutput, Table, ToolInvocation

    wide = ToolInvocation(
        tool="run_sql",
        arguments={},
        status="ok",
        output=RunSqlOutput(
            sql="SELECT 1",
            table=Table(
                columns=["n"],
                rows=[{"n": i} for i in range(50)],
                total_row_count=50,
            ),
        ),
        substrates_read=[],
    )
    truncated = summarize_invocation(wide, evidence_index=0, max_rows=10)
    assert len(truncated["output"]["table"]["rows"]) == 10
    assert "showing 10 of 50" in truncated["output"]["table"]["note"]


def test_echo_and_results_messages_shape():
    selections = [ToolSelection(name="run_sql", arguments={"question": "q"})]
    silent = LLMResponse(content="", tool_calls=[], model="scripted")
    echo = assistant_echo(silent, selections)
    assert echo.role == "assistant"
    assert 'run_sql({"question": "q"})' in echo.content

    spoken = LLMResponse(content="Let me check.", tool_calls=[], model="scripted")
    assert assistant_echo(spoken, selections).content == "Let me check."

    message = results_message(
        [{"evidence_index": 0, "tool": "run_sql", "status": "ok"}],
        notes=["There is no tool named 'x'."],
    )
    assert message.role == "user"
    first_line, payload, note = message.content.split("\n")
    assert first_line == "Tool results:"
    assert json.loads(payload)["tool"] == "run_sql"
    assert note.startswith("There is no tool")


def test_router_call_offers_all_real_and_control_specs(tool_pack):
    from engine.config.models import PortName
    from tests.harness_support import build_ask_session, tool_call

    session, ports, _ = build_ask_session(
        tool_pack, [tool_call("refuse", {"reason": "r"})]
    )
    session.ask("q")
    llm = ports.get(PortName.LLM)
    offered = [spec.name for spec in llm.calls[0]["tools"]]
    assert len(offered) == 9 + 4  # the closed surface + control verbs
    assert "app_primer" in offered and "give_answer" in offered
    assert llm.calls[0]["temperature"] == 0.0


def test_router_prompt_states_the_altitude_ladder_truthfully():
    """The prompt is LLM-facing surface; these lines are the phasing
    done-checks in prompt form. If traversal mechanics change, this
    test forces the prompt to change with them."""
    from engine.harness.prompts import render_router_prompt

    prompt = render_router_prompt(
        app_name="invoiceguard", app_description="d", max_iterations=6
    )
    # L0 questions go to the primer and never the graph.
    assert "app_primer" in prompt
    assert "never the code knowledge graph" in prompt
    # Traversal truth: component -> functions is two hops.
    assert "members, then contains" in prompt
    # Result-set questions steer to the untouched table envelope —
    # mandatory, not preferred (the prose-vs-table choice was the
    # carryback's #3b bug surface).
    assert "MUST call give_answer with shape='table'" in prompt
    # Fail-closed is a correct outcome, and the loop is bounded.
    assert "Refusing is a correct outcome" in prompt
    assert "at most 6 steps" in prompt
    # Protocol hardening: both observed violation classes are named
    # and priced in the step budget.
    assert "tool calls only" in prompt
    assert "costs one of your steps" in prompt
    # Addendum N5: the hardened prompt over-corrected into early
    # surrender. The retry after a steering error was a permission
    # and fired 1/5 on n13-witnesses (P-N11); it is now an expectation,
    # still bounded to exactly one, still tool-call form only — no
    # violation class reopened.
    assert "your next step MUST be one retry with a corrected tool call" in prompt
    assert "Exactly one retry" in prompt
    # Fix pass 3: clarify has a concrete trigger — 287 live turns
    # without a firing settled that "if ambiguous" alone never does.
    assert "two materially different readings" in prompt
    assert "rather than choosing a reading silently" in prompt


def test_dictionary_terms_resolve_from_the_real_pack(tool_pack):
    # Addendum N6, the runtime half: the fixture map's concept
    # synonym and metric name reach the composed router prompt.
    from engine.config.pack_loader import load_pack
    from engine.runtime.tools import resolve_data_terms
    from tests.conftest import build_tool_registry as _build

    _, ports = _build(tool_pack)
    terms = resolve_data_terms(load_pack(tool_pack), ports)
    assert "bill" in terms  # concept synonym
    assert "flag_rate" in terms  # metric name


def test_router_prompt_ties_business_terms_to_run_sql():
    # Addendum N6: "which rule produces the most savings" routed away
    # from run_sql twice because the Dictionary Map's vocabulary never
    # informed routing. The rendered prompt now names the terms inside
    # the run_sql guidance. (The route itself is the scripted LLM's
    # input in this harness, so prompt truth is the pinnable half; the
    # synonym-pair route assertion is 4b eval-bank territory.)
    from engine.harness.prompts import render_router_prompt

    prompt = render_router_prompt(
        app_name="a",
        app_description="d",
        max_iterations=6,
        data_terms=["opportunity", "savings", "flag_rate"],
    )
    bullet = prompt.split("run_sql.", 1)[1]
    assert "savings" in bullet
    assert "are still run_sql questions" in bullet

    without = render_router_prompt(
        app_name="a", app_description="d", max_iterations=6
    )
    assert "still run_sql questions" not in without


def test_router_prompt_renders_data_coverage_only_when_given():
    from engine.harness.prompts import render_router_prompt

    without = render_router_prompt(
        app_name="a", app_description="d", max_iterations=6
    )
    assert "execution log covers" not in without

    prompt = render_router_prompt(
        app_name="a",
        app_description="d",
        max_iterations=6,
        data_coverage=("2026-03-02", "2026-05-30"),
    )
    assert "The execution log covers 2026-03-02 through 2026-05-30" in prompt
    assert "never guess a year" in prompt


def test_router_messages_order_system_history_question_scratch():
    messages = build_router_messages(
        "SYSTEM",
        history=[Message(role="user", content="earlier q")],
        question="current q",
        scratch=[Message(role="assistant", content="Requested: app_primer({})")],
    )
    assert [m.role for m in messages] == ["system", "user", "user", "assistant"]
    assert messages[0].content == "SYSTEM"
    assert messages[2].content == "current q"
