"""run_sql: grounding, the execute–check–repair loop, guards, and the
evidence trail — all with the scripted LLM (no network, as always)."""

import pytest

from engine.config.models import PortName
from engine.ports.types import LLMResponse
from engine.tools.run_sql import extract_sql, guard_select_only

from tests.conftest import build_tool_registry
from tests.golden_grounding import GOLDEN, render_snapshot_grounding

BROKEN = LLMResponse(
    content="```sql\nSELECT COUNT(*) AS n FROM invoces\n```", model="scripted"
)
REPAIRED = LLMResponse(
    content="```sql\nSELECT COUNT(*) AS n FROM invoices\n```", model="scripted"
)


def test_repair_loop_recovers_and_feeds_the_error_back(tool_pack):
    registry, ports = build_tool_registry(tool_pack, [BROKEN, REPAIRED])
    invocation = registry.invoke("run_sql", {"question": "how many invoices?"})

    assert invocation.status == "ok", invocation.error
    assert invocation.output.sql == "SELECT COUNT(*) AS n FROM invoices"
    assert invocation.output.table.rows == [{"n": 50}]  # the snapshot slice
    assert invocation.output.table.total_row_count == 1

    # Both attempts retained, the loser with its error.
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].sql == "SELECT COUNT(*) AS n FROM invoces"
    assert "invoces" in attempts[0].error
    assert attempts[1].error is None
    assert attempts[1].row_count == 1

    # The DuckDB error text went back to the LLM verbatim.
    stub = ports.get(PortName.LLM)
    assert len(stub.calls) == 2
    second_call_messages = stub.calls[1]["messages"]
    assert attempts[0].error in second_call_messages[-1].content
    assert second_call_messages[-2].content == BROKEN.content


def test_unaliased_aggregate_triggers_a_repair_round(tool_pack):
    """Carryback #3b: DuckDB's default aggregate names (count_star())
    are unaddressable by the placeholder grammar, so a correct result
    was refused downstream. The guard now spends a repair round on an
    AS alias instead."""
    unaliased = LLMResponse(
        content="```sql\nSELECT COUNT(*) FROM invoices\n```", model="scripted"
    )
    registry, ports = build_tool_registry(tool_pack, [unaliased, REPAIRED])
    invocation = registry.invoke("run_sql", {"question": "how many invoices?"})

    assert invocation.status == "ok", invocation.error
    assert invocation.output.table.rows == [{"n": 50}]

    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert "AS alias" in attempts[0].error
    assert "count_star()" in attempts[0].error
    assert attempts[0].row_count == 1  # it ran; the name was the problem
    assert attempts[1].error is None

    stub = ports.get(PortName.LLM)
    assert attempts[0].error in stub.calls[1]["messages"][-1].content


def test_zero_row_results_skip_the_alias_guard(tool_pack):
    empty = LLMResponse(
        content="```sql\nSELECT id + 1 FROM invoices WHERE 1 = 0\n```",
        model="scripted",
    )
    registry, _ = build_tool_registry(tool_pack, [empty])
    invocation = registry.invoke("run_sql", {"question": "nothing"})
    assert invocation.status == "ok", invocation.error
    assert invocation.output.table.rows == []


def test_alias_criterion_matches_the_placeholder_segment_grammar():
    """The tools layer duplicates the placeholder name grammar rather
    than importing harness; this pin makes drift a test failure."""
    from engine.harness.placeholders import _SEGMENT
    from engine.tools.run_sql import _ADDRESSABLE_COLUMN

    name_grammar = _ADDRESSABLE_COLUMN.pattern.strip("^$")
    assert _SEGMENT.pattern == rf"^({name_grammar})((?:\[\d+\])*)$"


def test_grounding_prompt_matches_the_golden_fixture(
    tool_pack, snapshot_outputs
):
    """Pinned like generator output: a changed rendering changes this
    fixture deliberately (uv run python -m tests.golden_grounding --write)."""
    rendered = render_snapshot_grounding(snapshot_outputs)
    assert rendered == GOLDEN.read_text(encoding="utf-8")

    registry, ports = build_tool_registry(tool_pack, [REPAIRED])
    invocation = registry.invoke("run_sql", {"question": "how many invoices?"})
    assert invocation.evidence.grounding_prompt == rendered
    stub = ports.get(PortName.LLM)
    assert stub.calls[0]["messages"][0].content == rendered
    assert stub.calls[0]["temperature"] == 0.0


def test_grounding_carries_map_gotchas_and_metrics(snapshot_outputs):
    rendered = render_snapshot_grounding(snapshot_outputs)
    assert "adjustment_totals" in rendered  # the planted-story gotcha
    assert "flag_rate" in rendered
    assert "invoices.id = findings.invoice_id" in rendered


def test_exhausted_repairs_return_error_with_full_evidence(tool_pack):
    registry, _ = build_tool_registry(tool_pack, [BROKEN, BROKEN, BROKEN])
    invocation = registry.invoke("run_sql", {"question": "how many invoices?"})
    assert invocation.status == "error"
    assert "3 attempt(s)" in invocation.error
    assert len(invocation.evidence.attempts) == 3
    assert all(a.error for a in invocation.evidence.attempts)


def test_non_select_statements_are_rejected_not_executed(tool_pack):
    drop = LLMResponse(content="```sql\nDROP TABLE invoices\n```", model="scripted")
    registry, _ = build_tool_registry(tool_pack, [drop, drop, drop])
    invocation = registry.invoke("run_sql", {"question": "clean up"})
    assert invocation.status == "error"
    assert all(
        "read-only SELECT" in attempt.error
        for attempt in invocation.evidence.attempts
    )
    # And the table is still there.
    registry2, _ = build_tool_registry(tool_pack, [REPAIRED])
    assert registry2.invoke("run_sql", {"question": "count"}).status == "ok"


def test_response_without_sql_counts_as_a_failed_attempt(tool_pack):
    chatty = LLMResponse(
        content="I would need to know more about your schema.", model="scripted"
    )
    registry, _ = build_tool_registry(tool_pack, [chatty, REPAIRED])
    invocation = registry.invoke("run_sql", {"question": "how many invoices?"})
    assert invocation.status == "ok"
    assert invocation.evidence.attempts[0].sql is None
    assert "No SQL statement" in invocation.evidence.attempts[0].error


def test_result_rows_truncate_visibly(tool_pack):
    import yaml

    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["tool_settings"] = {"run_sql": {"max_result_rows": 10}}
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    wide = LLMResponse(
        content="```sql\nSELECT id FROM invoices ORDER BY id\n```",
        model="scripted",
    )
    registry, _ = build_tool_registry(tool_pack, [wide])
    invocation = registry.invoke("run_sql", {"question": "list invoice ids"})
    table = invocation.output.table
    assert table.truncated is True
    assert len(table.rows) == 10
    assert table.total_row_count == 50


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```\nWITH x AS (SELECT 1) SELECT * FROM x;\n```", "WITH x AS (SELECT 1) SELECT * FROM x"),
        ("-- a comment\nSELECT 2", "SELECT 2"),
        ("SELECT 3;", "SELECT 3"),
        ("Sure! Here is prose only.", None),
        ("", None),
    ],
)
def test_extract_sql(text, expected):
    assert extract_sql(text) == expected


def test_guard_rejects_multi_statement():
    assert guard_select_only("SELECT 1; DROP TABLE x") is not None
    assert guard_select_only("DELETE FROM invoices") is not None
    assert guard_select_only("SELECT 1") is None
    assert guard_select_only("WITH x AS (SELECT 1) SELECT * FROM x") is None
