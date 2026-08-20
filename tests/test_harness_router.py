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
    # Result-set questions steer to the untouched table envelope.
    assert "shape='table'" in prompt
    # Fail-closed is a correct outcome, and the loop is bounded.
    assert "Refusing is a correct outcome" in prompt
    assert "at most 6 steps" in prompt


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
