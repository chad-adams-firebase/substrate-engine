"""Prose drafting: temperature 0, evidence-only context, placeholder
resolution; and the table projections for pass-through answers."""

from engine.config.models import PortName
from engine.harness.drafter import (
    Drafter,
    build_drafting_messages,
    render_evidence,
)
from engine.harness.tables import caption_for, project_table
from engine.ports.types import LLMResponse, RunStatus
from engine.tools.envelope import CheckExecutionOutput, ToolInvocation
from tests.conftest import build_tool_registry


def _evidence(tool_pack, llm_responses=None):
    registry, ports = build_tool_registry(tool_pack, llm_responses or [])
    return registry, ports


def test_draft_resolves_placeholders_from_real_evidence(tool_pack):
    draft_text = (
        "Invoices table has {{e0.rows[0].row_count}} rows; status has "
        "{{e0.rows[0].distinct_count}} distinct values."
    )
    registry, ports = _evidence(
        tool_pack, [LLMResponse(content=draft_text, model="scripted")]
    )
    invocation = registry.invoke(
        "query_univariate_stats", {"table": "invoices", "column": "status"}
    )
    stub = ports.get(PortName.LLM)
    drafter = Drafter(stub, "DRAFT SYSTEM")
    result = drafter.draft("how big is invoices?", [invocation])

    assert result.resolution.failures == []
    assert "50 rows" in result.resolution.text  # the snapshot world
    assert stub.calls[0]["temperature"] == 0.0
    assert stub.calls[0]["tools"] is None


def test_drafter_context_is_outputs_only_never_residue(tool_pack):
    # search_business_docs retains full sections as evidence residue;
    # the drafter must see the output (hits) and not the residue.
    registry, ports = _evidence(
        tool_pack, [LLMResponse(content="ok", model="scripted")]
    )
    invocation = registry.invoke(
        "search_business_docs", {"query": "rate variance threshold"}
    )
    assert invocation.evidence is not None  # residue exists...
    stub = ports.get(PortName.LLM)
    Drafter(stub, "SYS").draft("why 15%?", [invocation])

    sent = stub.calls[0]["messages"][1].content
    assert '"output"' in sent
    assert '"sections"' not in sent  # ...and never reaches the model


def test_errored_invocations_render_collapsed_without_error_text():
    """Fix pass 4 (gate verdict N11): the drafter anchored on an
    errored invocation, recited its content-rich error text, and
    shipped "no information" while the answer sat in the clean one.
    Failed calls render collapsed — the verifier harvests nothing
    from a non-ok invocation, so every echoed token was guaranteed
    unmatched; the router already saw the error in its own loop."""
    error_text = (
        "Unknown component 'benchmark'. Known components: "
        "invoiceguard.benchmark_scoring, invoiceguard.stale_sweep"
    )
    errored = ToolInvocation(
        tool="check_execution", arguments={}, status="error", error=error_text
    )
    clean = ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(
            run_status=RunStatus(ran=True, count=1, detail="1 event(s)")
        ),
    )
    first, second = render_evidence([errored, clean]).splitlines()
    assert '"status":"error"' in first
    assert "Known components" not in first
    assert '"output"' not in first
    assert '"note":"call failed; supports no citations or placeholders"' in first
    assert '"status":"ok"' in second and '"run_status"' in second


def test_none_valued_fields_are_suppressed_from_the_rendered_view():
    """Fix-pass-4 follow-up (HN-ERRORS): a mode's unused half rendered
    as null visually corroborates an emptiness misreading — shown
    {"error_count": 0, "errors": [], "run_status": null}, the drafter
    disclaimed the count it had. None fields never render; a present
    0 and an empty list do."""
    clean = ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(error_count=0, errors=[]),
    )
    (line,) = render_evidence([clean]).splitlines()
    assert '"run_status"' not in line
    assert '"error_count":0' in line
    assert '"errors":[]' in line


def test_feedback_appends_previous_draft_and_instructions():
    messages = build_drafting_messages(
        "SYS",
        "q",
        [],
        previous_draft="There were 14,600 invoices.",
        feedback=['"14,600" is not supported; closest value: 146'],
    )
    assert [m.role for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[2].content == "There were 14,600 invoices."
    assert "failed verification" in messages[3].content
    assert "14,600" in messages[3].content


def test_project_table_for_the_three_data_shaped_outputs(tool_pack):
    registry, _ = _evidence(tool_pack)
    stats = registry.invoke("query_univariate_stats", {"table": "invoices"})
    projected = project_table(stats.output)
    assert projected is not None
    assert "column_name" in projected.columns
    assert "provenance" not in projected.columns
    assert projected.total_row_count == len(stats.output.rows)

    dictionary = registry.invoke("lookup_data_dictionary", {"table": "invoices"})
    assert project_table(dictionary.output) is not None

    primer = registry.invoke("app_primer", {})
    assert project_table(primer.output) is None  # not table-shaped


def test_run_sql_table_passes_through_identical(tool_pack):
    from engine.tools.envelope import RunSqlOutput, Table

    output = RunSqlOutput(
        sql="SELECT COUNT(*) AS n FROM invoices",
        table=Table(columns=["n"], rows=[{"n": 146}], total_row_count=1),
    )
    assert project_table(output) is output.table  # the same object
    assert caption_for(output) == "SELECT COUNT(*) AS n FROM invoices"


def test_harvested_field_names_are_exactly_the_rendered_keys():
    """N13: the verifier's field-name harvest and the drafter's view
    are one function (ToolInvocation.rendered_output) — what the
    drafter can read is exactly what it may cite, None-suppressed
    fields included in neither."""
    import json

    from engine.verifier.checks.invocation import field_names

    invocation = ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(errors=[], error_count=0),
    )
    rendered = json.loads(render_evidence([invocation]))["output"]
    assert field_names(rendered) == field_names(invocation.rendered_output())
    assert "error_count" in field_names(rendered)
    assert "run_status" not in field_names(rendered)
