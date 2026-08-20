"""Control-tool specs and route parsing: every terminal direction
arrives schema-validated; evidence-gathering wins over control."""

import pytest

from engine.harness.control import (
    CONTROL_NAMES,
    RouteProtocolViolation,
    control_specs,
    parse_route,
)
from engine.ports.types import LLMResponse, ToolCall


def _response(*calls: ToolCall, content: str = "") -> LLMResponse:
    return LLMResponse(content=content, tool_calls=list(calls), model="scripted")


def test_control_specs_are_llm_ready():
    specs = control_specs()
    assert [s.name for s in specs] == sorted(CONTROL_NAMES)
    for spec in specs:
        assert spec.description
        assert spec.input_schema["type"] == "object"


def test_real_tool_calls_become_a_tools_decision_in_order():
    decision = parse_route(
        _response(
            ToolCall(name="app_primer", arguments={}),
            ToolCall(name="run_sql", arguments={"question": "how many?"}),
        )
    )
    assert decision.kind == "tools"
    assert [s.name for s in decision.selections] == ["app_primer", "run_sql"]


def test_real_calls_win_over_control_calls_in_the_same_response():
    # A model asking for run_sql AND give_answer has not seen the rows.
    decision = parse_route(
        _response(
            ToolCall(name="run_sql", arguments={"question": "q"}),
            ToolCall(name="give_answer", arguments={"shape": "prose"}),
        )
    )
    assert decision.kind == "tools"
    assert [s.name for s in decision.selections] == ["run_sql"]


def test_control_only_maps_to_typed_decisions():
    answer = parse_route(
        _response(
            ToolCall(
                name="give_answer",
                arguments={"shape": "table", "evidence_index": 2},
            )
        )
    )
    assert answer.kind == "answer"
    assert answer.answer_shape == "table" and answer.evidence_index == 2

    refuse = parse_route(
        _response(
            ToolCall(
                name="refuse",
                arguments={"reason": "out of scope", "what_would_work": "data qs"},
            )
        )
    )
    assert refuse.kind == "refuse"
    assert refuse.reason == "out of scope"
    assert refuse.what_would_work == "data qs"

    clarify = parse_route(
        _response(ToolCall(name="clarify", arguments={"question": "which week?"}))
    )
    assert clarify.kind == "clarify" and clarify.question == "which week?"

    escalate = parse_route(
        _response(ToolCall(name="escalate", arguments={"reason": "policy call"}))
    )
    assert escalate.kind == "escalate" and escalate.reason == "policy call"


def test_prose_only_response_is_a_protocol_violation():
    with pytest.raises(RouteProtocolViolation, match="calling one of"):
        parse_route(_response(content="The answer is probably 42."))


def test_malformed_control_arguments_are_a_protocol_violation():
    with pytest.raises(RouteProtocolViolation, match="give_answer"):
        parse_route(_response(ToolCall(name="give_answer", arguments={"shape": "table"})))
    with pytest.raises(RouteProtocolViolation, match="refuse"):
        parse_route(_response(ToolCall(name="refuse", arguments={})))
