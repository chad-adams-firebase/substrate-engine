"""run_sql: grounding, the execute–check–repair loop, guards, and the
evidence trail — all with the scripted LLM (no network, as always)."""

import pytest

from engine.config.models import PortName
from engine.ports.types import LLMResponse
from engine.tools.run_sql import extract_sql, guard_select_only

from tests.conftest import build_tool_registry
from tests.golden_grounding import (
    GOLDEN,
    GOLDEN_METRIC,
    METRIC_QUESTION,
    render_snapshot_grounding,
)

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


FANOUT = LLMResponse(
    content=(
        "```sql\nSELECT COUNT(*) AS n FROM invoices i "
        "JOIN findings f ON f.invoice_id = i.id\n```"
    ),
    model="scripted",
)
DISTINCT = LLMResponse(
    content=(
        "```sql\nSELECT COUNT(DISTINCT i.id) AS n FROM invoices i "
        "JOIN findings f ON f.invoice_id = i.id\n```"
    ),
    model="scripted",
)


def test_fan_out_lint_draws_one_repair_round_before_execution(tool_pack):
    """Fix pass 3 (4b baseline MT2): COUNT(*) over a join to the many
    side is challenged before it runs; the corrected statement
    executes. The lint's error text reaches the LLM verbatim."""
    registry, ports = build_tool_registry(tool_pack, [FANOUT, DISTINCT])
    invocation = registry.invoke("run_sql", {"question": "how many flagged?"})

    assert invocation.status == "ok", invocation.error
    assert invocation.output.sql.startswith("SELECT COUNT(DISTINCT i.id)")
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].error.startswith("Fan-out check:")
    assert attempts[0].lint == attempts[0].error  # typed challenge marker
    assert attempts[0].row_count is None  # never executed
    assert attempts[1].error is None
    assert attempts[1].lint is None  # repaired: the re-lint is clean
    stub = ports.get(PortName.LLM)
    assert attempts[0].error in stub.calls[1]["messages"][-1].content


def test_fan_out_lint_fires_once_and_licenses_resend_unchanged(tool_pack):
    """The lint's word is a repair round, not a verdict: the same
    statement resent is the model's considered answer and runs — but
    the override is no longer invisible: the detection-only re-lint
    records the still-tripping reason on the executed attempt, which
    the Verifier reads as a plausibility warn."""
    registry, _ = build_tool_registry(tool_pack, [FANOUT, FANOUT])
    invocation = registry.invoke("run_sql", {"question": "how many flagged?"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].error.startswith("Fan-out check:")
    assert attempts[1].error is None and attempts[1].row_count == 1
    assert attempts[1].lint is not None
    assert attempts[1].lint.startswith("Fan-out check:")


DEAD_LEFT = LLMResponse(
    content=(
        "```sql\nSELECT COUNT(DISTINCT i.id) AS n FROM invoices i "
        "LEFT JOIN findings f ON f.invoice_id = i.id\n```"
    ),
    model="scripted",
)


def test_join_shape_lint_rides_the_same_challenge_machinery(tool_pack):
    """Pin pass: the new join-shape reasons (dead LEFT JOIN, AVG over
    a LEFT JOIN's null side) block, re-lint, and record overrides
    exactly like the fan-out reasons — no new plumbing in run_sql."""
    registry, _ = build_tool_registry(tool_pack, [DEAD_LEFT, DISTINCT])
    invocation = registry.invoke("run_sql", {"question": "how many flagged?"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].error.startswith("Join-shape check:")
    assert attempts[0].lint == attempts[0].error
    assert attempts[0].row_count is None  # blocked before execution
    assert attempts[1].error is None
    assert attempts[1].lint is None  # the inner-join repair is clean


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
    # The bracket suffix is the placeholder's own (indices may be
    # negative since the Polish Pass); the pin is the name grammar.
    assert _SEGMENT.pattern == rf"^({name_grammar})((?:\[-?\d+\])*)$"


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
    assert "half-open range" in rendered  # the window convention (C4)
    # The coverage pass's header rules (Play Session #2: F1, S-C, S-A).
    assert "return NULL, not 0" in rendered
    assert "Every LIMIT follows an ORDER BY" in rendered
    assert "fractions in [0, 1]" in rendered


def test_a_question_naming_a_metric_gets_its_template_first(
    tool_pack, snapshot_outputs
):
    """Fix pass 3 (4b baseline U5): the right prose sat in the prompt
    and was ignored, so a matched metric's definition now leads the
    prompt as the statement template — pinned by its own golden
    (uv run python -m tests.golden_grounding --write). A question
    naming no metric renders exactly as before."""
    rendered = render_snapshot_grounding(snapshot_outputs, METRIC_QUESTION)
    assert rendered == GOLDEN_METRIC.read_text(encoding="utf-8")
    head, _, rest = rendered.partition("## Tables and columns")
    assert "## Canonical template for this question — metric flag_rate" in head
    assert "COUNT(DISTINCT f.invoice_id) * 1.0" in head
    assert "adapt only the WHERE filters" in head
    assert "## Canonical template" not in rest
    assert render_snapshot_grounding(snapshot_outputs, "how many invoices?") == (
        GOLDEN.read_text(encoding="utf-8")
    )

    registry, ports = build_tool_registry(tool_pack, [REPAIRED])
    registry.invoke("run_sql", {"question": METRIC_QUESTION})
    stub = ports.get(PortName.LLM)
    assert stub.calls[0]["messages"][0].content == rendered


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


def test_sql_shaped_question_is_a_steering_error_before_any_work(tool_pack):
    # Addendum hygiene: the router leaked its own hallucinated SQL
    # into the question argument 2-for-2. SQL shapes bounce with a
    # steering error before the substrate loads or the LLM runs...
    registry, _ = build_tool_registry(tool_pack, [])
    leaked = registry.invoke(
        "run_sql",
        {"question": "SELECT COUNT(*) FROM invoices WHERE status = 'READY'"},
    )
    assert leaked.status == "error"
    assert "English" in leaked.error
    assert leaked.evidence is None  # no attempt was spent

    # ...while an English imperative that merely starts with "Select"
    # and contains "from" passes the case-sensitive heuristic.
    registry2, _ = build_tool_registry(tool_pack, [REPAIRED])
    english = registry2.invoke(
        "run_sql", {"question": "Select the invoices from last week"}
    )
    assert english.status == "ok", english.error


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


def test_result_tables_carry_money_hints_from_the_map_and_pack(tool_pack):
    """§10.5 / NP3: the tool tags money columns — declared ones and
    aggregate aliases ending in them — from the Dictionary Map's
    column_formats and the pack's display.money; nothing in engine
    code names a column."""
    query = LLMResponse(
        content=(
            "```sql\nSELECT COUNT(*) AS backlog_count, "
            "SUM(opportunity) AS total_opportunity, "
            "AVG(opportunity) AS opportunity_rate, opportunity "
            "FROM invoices GROUP BY opportunity LIMIT 1\n```"
        ),
        model="scripted",
    )
    registry, _ = build_tool_registry(tool_pack, [query])
    invocation = registry.invoke("run_sql", {"question": "backlog and opportunity"})
    assert invocation.status == "ok", invocation.error
    formats = invocation.output.table.column_formats
    # Parse-first (the coverage pass): AVG over a money column is
    # money whatever the alias says — the _rate marker vetoes only
    # aliases the statement cannot trace (test_column_formats).
    assert {name: hint.kind for name, hint in formats.items()} == {
        "total_opportunity": "money",
        "opportunity": "money",
        "opportunity_rate": "money",
    }
    assert formats["opportunity"].symbol == "$"


def test_no_display_money_block_means_untagged_tables(tool_pack):
    import yaml

    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config.pop("display")
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    query = LLMResponse(
        content="```sql\nSELECT SUM(opportunity) AS total_opportunity FROM invoices\n```",
        model="scripted",
    )
    registry, _ = build_tool_registry(tool_pack, [query])
    invocation = registry.invoke("run_sql", {"question": "total opportunity"})
    assert invocation.output.table.column_formats == {}


def test_grounding_states_the_order_by_rule():
    """Block 2 (S5's row reshuffle on every follow-up): a grouped
    result carries an ORDER BY, so the same question returns rows in
    the same order and "the third supplier" means the same supplier
    next turn. One grounding line — the golden fixture carries it."""
    from tests.golden_grounding import GOLDEN

    text = GOLDEN.read_text(encoding="utf-8")
    assert "A query with GROUP BY always carries an ORDER BY" in text
    assert "follow-ups refer to rows" in text


def test_the_statement_reaches_the_display_resolver(tool_pack):
    """Parse-first: an alias that says nothing (mean_value) is money
    because the SQL averaged a money column; the same alias without
    the SQL would carry no hint."""
    from engine.ports.types import LLMResponse

    registry, _ = build_tool_registry(
        tool_pack,
        [
            LLMResponse(
                content=(
                    "```sql\nSELECT AVG(i.opportunity) AS mean_value, "
                    "COUNT(*) AS total_amount FROM invoices i\n```"
                ),
                model="scripted",
            )
        ],
    )
    invocation = registry.invoke("run_sql", {"question": "average opportunity?"})
    formats = invocation.output.table.column_formats
    assert formats["mean_value"].kind == "money"
    assert "total_amount" not in formats  # a COUNT, whatever the alias says


ENUM_BAD = LLMResponse(
    content=(
        "```sql\nSELECT ih.actor AS reviewer, COUNT(*) AS rejection_count "
        "FROM invoice_history ih WHERE ih.to_status = 'REJECTED' "
        "GROUP BY ih.actor ORDER BY rejection_count DESC\n```"
    ),
    model="scripted",
)
ENUM_GOOD = LLMResponse(
    content=(
        "```sql\nSELECT ih.actor AS reviewer, COUNT(*) AS closed_count "
        "FROM invoice_history ih WHERE ih.to_status = 'CLOSED' "
        "GROUP BY ih.actor ORDER BY closed_count DESC\n```"
    ),
    model="scripted",
)
BOTH_BAD = LLMResponse(
    content=(
        "```sql\nSELECT COUNT(*) AS n FROM invoices i "
        "JOIN findings f ON f.invoice_id = i.id "
        "WHERE i.status = 'IN_REVIEW'\n```"
    ),
    model="scripted",
)


def test_enum_literal_lint_draws_one_repair_round_naming_the_values(tool_pack):
    """Play Session #2's R-A: to_status = 'REJECTED' is not a status.
    The challenge names the observed values before anything executes;
    the corrected statement runs clean."""
    registry, ports = build_tool_registry(tool_pack, [ENUM_BAD, ENUM_GOOD])
    invocation = registry.invoke("run_sql", {"question": "rejections by reviewer"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].error.startswith("Enum check: `invoice_history.to_status` never takes 'REJECTED'")
    assert "observed values:" in attempts[0].error
    assert attempts[0].enum_lint == attempts[0].error
    assert attempts[0].lint is None and attempts[0].row_count is None
    assert attempts[1].error is None and attempts[1].enum_lint is None
    stub = ports.get(PortName.LLM)
    assert attempts[0].error in stub.calls[1]["messages"][-1].content


def test_enum_literal_override_is_recorded_on_the_executed_attempt(tool_pack):
    registry, _ = build_tool_registry(tool_pack, [ENUM_BAD, ENUM_BAD])
    invocation = registry.invoke("run_sql", {"question": "rejections by reviewer"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert attempts[1].error is None
    assert attempts[1].row_count == 0  # the value never occurs: no rows
    assert attempts[1].enum_lint.startswith("Enum check:")
    assert invocation.output.table.rows == []


def test_both_lints_challenge_together_in_one_round(tool_pack):
    """A statement that fans AND filters on a phantom value gets one
    repair round carrying both reasons — the budget is not spent twice."""
    registry, _ = build_tool_registry(tool_pack, [BOTH_BAD, BOTH_BAD])
    invocation = registry.invoke("run_sql", {"question": "in-review flagged?"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert "Fan-out check:" in attempts[0].error and "Enum check:" in attempts[0].error
    assert "`invoices.status` never takes 'IN_REVIEW'" in attempts[0].error
    assert "Keep the query on `invoices`" in attempts[0].error
    # The guard pass: the challenge never names the table that does
    # hold the value — AMB2's rep 1 read that as a destination.
    assert "invoice_history" not in attempts[0].enum_lint
    assert attempts[0].lint and attempts[0].enum_lint
    # The resend executes with both override traces.
    assert attempts[1].error is None
    assert attempts[1].lint and attempts[1].enum_lint


INTERVAL_SCALED = LLMResponse(
    content=(
        "```sql\nSELECT AVG(i.scored_at - i.received_at) / 3600 AS avg_hours "
        "FROM invoices i\n```"
    ),
    model="scripted",
)
INTERVAL_EPOCH = LLMResponse(
    content=(
        "```sql\nSELECT AVG(EPOCH(i.scored_at - i.received_at)) / 3600 AS avg_hours "
        "FROM invoices i\n```"
    ),
    model="scripted",
)
TRIPLE_BAD = LLMResponse(
    content=(
        "```sql\nSELECT COUNT(*) AS n, AVG(h.at - i.received_at) / 3600 AS avg_hours "
        "FROM invoices i JOIN invoice_history h ON h.invoice_id = i.id "
        "WHERE h.to_status = 'REJECTED'\n```"
    ),
    model="scripted",
)


def test_interval_lint_draws_one_repair_round_before_execution(tool_pack):
    """Duration pass (post-coverage W3 rep 4): an interval divided by
    3600 is challenged before it runs; the EPOCH-first statement
    executes clean."""
    registry, ports = build_tool_registry(tool_pack, [INTERVAL_SCALED, INTERVAL_EPOCH])
    invocation = registry.invoke("run_sql", {"question": "how long to score?"})
    assert invocation.status == "ok", invocation.error
    assert "EPOCH" in invocation.output.sql
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    assert attempts[0].error.startswith("Interval-arithmetic check: `avg_hours`")
    assert attempts[0].interval_lint == attempts[0].error
    assert attempts[0].lint is None and attempts[0].enum_lint is None
    assert attempts[0].row_count is None  # never executed
    assert attempts[1].error is None and attempts[1].interval_lint is None
    stub = ports.get(PortName.LLM)
    assert attempts[0].error in stub.calls[1]["messages"][-1].content


def test_interval_override_is_recorded_on_the_executed_attempt(tool_pack):
    registry, _ = build_tool_registry(tool_pack, [INTERVAL_SCALED, INTERVAL_SCALED])
    invocation = registry.invoke("run_sql", {"question": "how long to score?"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert attempts[1].error is None and attempts[1].row_count == 1
    assert attempts[1].interval_lint.startswith("Interval-arithmetic check:")


def test_all_three_lints_challenge_together_in_one_round(tool_pack):
    registry, _ = build_tool_registry(tool_pack, [TRIPLE_BAD, TRIPLE_BAD])
    invocation = registry.invoke("run_sql", {"question": "rejected wait?"})
    assert invocation.status == "ok", invocation.error
    attempts = invocation.evidence.attempts
    assert len(attempts) == 2
    first = attempts[0].error
    assert "Fan-out check:" in first and "Enum check:" in first
    assert "Interval-arithmetic check:" in first
    assert attempts[0].lint and attempts[0].enum_lint and attempts[0].interval_lint
    assert attempts[1].error is None
    assert attempts[1].lint and attempts[1].enum_lint and attempts[1].interval_lint



def test_run_sql_description_licenses_the_english_retry_after_a_bounce():
    """The verbatim rule and its one exception travel together on the
    tool surface too: after a SQL bounce the retry is the question in
    plain English (post-coverage REC-SQL)."""
    from engine.tools.run_sql import RunSql

    assert "never a paraphrase" in RunSql.description
    assert "what the SQL asks" in RunSql.description
    assert "the licensed retry, not a paraphrase" in RunSql.description
