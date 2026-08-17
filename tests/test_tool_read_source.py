"""read_source: exact lines at the pinned commit, refusal on SHA drift."""

from pathlib import Path

import yaml

from tests.conftest import build_tool_registry

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"
RATE_VARIANCE = "invoiceguard.spine.rules_engine.rule_rate_variance"


def test_returns_the_exact_lines_of_the_rule_function(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("read_source", {"node": RATE_VARIANCE})
    assert invocation.status == "ok", invocation.error
    output = invocation.output
    assert output.qualified_name == RATE_VARIANCE
    assert (output.start_line, output.end_line) == (116, 149)

    vendored = (
        SNAPSHOT / "source" / output.file_path
    ).read_text(encoding="utf-8")
    expected = "".join(
        vendored.splitlines(keepends=True)[output.start_line - 1 : output.end_line]
    )
    assert output.text == expected
    assert "def rule_rate_variance(" in output.text
    assert output.commit_sha.startswith("761a18e9")
    assert invocation.evidence is None  # the output IS the raw record


def test_sha_drift_refuses_with_both_shas_named(tool_pack):
    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["adapters"]["source_code"]["settings"]["commit_sha"] = "f" * 40
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("read_source", {"node": RATE_VARIANCE})
    assert invocation.status == "error"
    assert "761a18e9b925" in invocation.error
    assert "ffffffffffff" in invocation.error


def test_unknown_node_points_at_the_traversal_tool(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("read_source", {"node": "no.such.function"})
    assert invocation.status == "error"
    assert "traverse_code_knowledge_graph" in invocation.error
