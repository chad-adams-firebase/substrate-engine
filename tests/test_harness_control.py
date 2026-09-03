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


def test_refuse_verb_does_not_invite_surrender_while_tools_remain():
    """Post-Block-2 W4: four of five reps refused after the primer and
    the documents returned no rule list, never reaching the code
    knowledge graph — the only router-facing Block 2 change was this
    description. The manager-language mandate for the card text stays."""
    from engine.harness.control import _CONTROL_DESCRIPTIONS

    refuse = _CONTROL_DESCRIPTIONS["refuse"]
    assert "code knowledge graph and source" in refuse
    assert "Refuse only when the tool surface is exhausted" in refuse
    assert "never because the first tool returned nothing" in refuse
    assert "plain language for a business reader" in refuse


def test_protocol_violations_carry_what_the_router_wrote():
    """The raw text goes to provenance so the next B2 diagnosis is one
    read; the message stays the short nudge."""
    with pytest.raises(RouteProtocolViolation) as prose:
        parse_route(LLMResponse(content="The answer is probably 42.", model="s"))
    assert prose.value.raw_response == "The answer is probably 42."
    assert "calling one of" in str(prose.value)

    with pytest.raises(RouteProtocolViolation) as malformed:
        parse_route(_response(ToolCall(name="refuse", arguments={})))
    assert malformed.value.raw_response == "refuse({})"


# --- Polish Pass: a control verb written as text is the call it is ----


def test_a_text_form_control_verb_is_the_call_it_plainly_is():
    """B2's nine-run root cause: the router wrote
    give_answer({"shape":"prose","evidence_index":3}) as text — the
    right call in the wrong channel — and was nudged four times into
    budget exhaustion. It parses as the call, and the decision says so."""
    decision = parse_route(
        _response(content='give_answer({"shape":"prose","evidence_index":3})')
    )
    assert (decision.kind, decision.answer_shape, decision.evidence_index) == (
        "answer", "prose", 3,
    )
    assert decision.parsed_from_text is True
    # The loop transcript's echo prefix, a bare call, and the other verbs.
    assert parse_route(_response(content='Requested: give_answer({"shape":"prose"})')).kind == "answer"
    assert parse_route(_response(content="give_answer()")).kind == "answer"
    refuse = parse_route(_response(content='refuse({"reason": "no", "what_would_work": "x"})'))
    assert (refuse.kind, refuse.reason, refuse.what_would_work) == ("refuse", "no", "x")
    assert parse_route(_response(content='clarify({"question": "which?"})')).question == "which?"
    assert parse_route(_response(content='escalate({"reason": "policy"})')).kind == "escalate"
    # A structured call is never marked lenient.
    assert parse_route(_response(ToolCall(name="refuse", arguments={"reason": "r"}))).parsed_from_text is False


def test_text_form_calls_still_validate_and_prose_still_nudges():
    with pytest.raises(RouteProtocolViolation, match="requires evidence_index") as info:
        parse_route(_response(content='give_answer({"shape":"table"})'))
    assert info.value.raw_response == 'give_answer({"shape": "table"})'
    for content in (
        "give_answer(shape=prose)",  # not JSON
        'The answer is give_answer({"shape":"prose"}).',  # not the whole response
        'traverse_code_knowledge_graph({"entry": "abc", "hop": "callees"})',  # a real tool
        "The answer is probably 42.",
    ):
        with pytest.raises(RouteProtocolViolation) as info:
            parse_route(_response(content=content))
        assert info.value.raw_response == content
