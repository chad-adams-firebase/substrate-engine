"""The tool registry: closed surface, never-raising invoke, build-time
dependency validation, and the envelope round-trip for every tool."""

import copy
from datetime import UTC, datetime

import pytest
import yaml

from engine.config.models import PortName, ToolName
from engine.config.pack_loader import load_pack
from engine.ports.llm import LLMPort
from engine.runtime.container import build
from engine.runtime.registry import default_registry
from engine.runtime.tools import ToolBuildError, build_tools
from engine.tools.envelope import dumps_turn_evidence, loads_turn_evidence
from engine.tools.registry import UnknownToolError

from tests.conftest import build_tool_registry
from tests.stubs.llm_stub import ScriptedLLM
from engine.ports.types import LLMResponse


def test_specs_cover_exactly_the_enabled_tools(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    specs = registry.to_specs()
    assert [spec.name for spec in specs] == sorted(t.value for t in ToolName)
    for spec in specs:
        assert spec.description
        assert spec.input_schema["type"] == "object"


def test_invoke_returns_error_envelope_for_bad_arguments(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("query_univariate_stats", {"tabel": "invoices"})
    assert invocation.status == "error"
    assert "tabel" in invocation.error
    assert invocation.output is None


def test_invoke_raises_only_for_harness_bugs(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    with pytest.raises(UnknownToolError):
        registry.invoke("run_spl", {})


def test_disabled_tool_is_not_registered(tool_pack):
    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["tools"] = ["app_primer"]
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    registry, _ = build_tool_registry(tool_pack)
    assert registry.names() == [ToolName.APP_PRIMER]
    with pytest.raises(UnknownToolError, match="app_primer"):
        registry.invoke("run_sql", {"question": "x"})


def test_missing_substrate_dependency_fails_at_build(tool_pack):
    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["substrates"].remove("data_dictionary_map")
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ToolBuildError, match="run_sql.*data_dictionary_map"):
        build_tool_registry(tool_pack)


def test_missing_port_dependency_fails_at_build(tool_pack):
    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    del config["adapters"]["execution_log"]
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ToolBuildError, match="check_execution.*execution_log"):
        build_tool_registry(tool_pack)


def test_every_tool_envelope_round_trips(tool_pack):
    """The Phase 4 no-retrofit guarantee, exercised through real
    invocations of all nine tools (ok and error paths alike)."""
    registry, _ = build_tool_registry(
        tool_pack,
        llm_responses=[
            LLMResponse(
                content="```sql\nSELECT COUNT(*) AS n FROM invoices\n```",
                model="scripted",
            )
        ],
    )
    day = {
        "window_start": datetime(2026, 3, 11, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 3, 12, tzinfo=UTC).isoformat(),
    }
    calls: list[tuple[str, dict]] = [
        ("query_univariate_stats", {"table": "invoices"}),
        ("lookup_data_dictionary", {"table": "invoices", "column": "status"}),
        (
            "traverse_code_knowledge_graph",
            {"entry": "invoiceguard.spine.rules_engine.run_rules", "hop": "callees"},
        ),
        ("read_source", {"node": "invoiceguard.spine.rules_engine.rule_rate_variance"}),
        ("run_sql", {"question": "how many invoices are there?"}),
        ("app_primer", {}),
        ("search_business_docs", {"query": "rate variance threshold"}),
        ("check_execution", {"component": "stale_sweep", **day}),
        ("answer_from_known_items", {"query": "flag rate"}),
        # And one error envelope in the same turn.
        ("query_univariate_stats", {"table": "no_such_table"}),
    ]
    turn = [registry.invoke(name, arguments) for name, arguments in calls]
    statuses = {invocation.tool: invocation.status for invocation in turn[:-1]}
    assert set(statuses.values()) == {"ok"}, statuses
    assert turn[-1].status == "error"

    text = dumps_turn_evidence(turn)
    restored = loads_turn_evidence(text)
    assert restored == turn
    assert dumps_turn_evidence(restored) == text


def test_registry_catch_all_converts_crashes_to_error_envelopes(tool_pack):
    class ExplodingLLM(LLMPort):
        def complete(self, messages, *, tools=None, temperature=0.0):
            raise RuntimeError("boom")

    pack = load_pack(tool_pack)
    registry = default_registry()
    registry.register(
        PortName.LLM, "openrouter", lambda settings, root: ExplodingLLM()
    )
    tools = build_tools(pack, build(pack, registry))
    invocation = tools.invoke("run_sql", {"question": "x"})
    assert invocation.status == "error"
    assert "boom" in invocation.error
