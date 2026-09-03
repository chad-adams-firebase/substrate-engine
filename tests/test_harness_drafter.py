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
    drafter = Drafter(stub, "DRAFT SYSTEM", inline_value_max_chars=120)
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
    Drafter(stub, "SYS", inline_value_max_chars=120).draft("why 15%?", [invocation])

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


def test_interpretation_rule_renders_only_when_declared():
    """Play pass C4 (W8): the drafter rule that makes an answer name
    the reading it used exists only for packs whose map declares
    interpretations — config-grounded, injected as text."""
    from engine.harness.prompts import render_drafter_prompt

    with_terms = render_drafter_prompt(
        app_name="a",
        interpretation_terms=(
            "  recovered_opportunity: closed-invoice opportunity "
            "(the rollup); feedback-authored findings (authored rows)"
        ),
    )
    assert "more than one reading" in with_terms
    assert "which reading the evidence's SQL used" in with_terms
    assert "closed-invoice opportunity" in with_terms

    without = render_drafter_prompt(app_name="a")
    assert "more than one reading" not in without


def test_interpretation_terms_resolve_from_the_fixture_map(tool_pack):
    from engine.config.pack_loader import load_pack
    from engine.runtime.tools import resolve_interpretation_terms
    from tests.conftest import build_tool_registry as _build

    _, ports = _build(tool_pack)
    terms = resolve_interpretation_terms(load_pack(tool_pack), ports)
    assert terms is not None
    # The synonyms ride beside the term (Polish Pass), so a drafter that
    # wrote the synonym connects it to the readings.
    assert "flag_rate (also: flagged share, share flagged):" in terms
    assert "substantive" in terms


def test_drafter_prompt_carries_the_block_2_rendering_rules():
    """Block 2: quoted source lives in a labeled fenced block that
    opens on the def line (B4's rep 1 opened on the docstring), and
    placeholders inject values, never passages (O1)."""
    from engine.harness.prompts import render_drafter_prompt

    prompt = render_drafter_prompt(app_name="a")
    assert "inside a fenced code block labeled with its language" in prompt
    assert "starts at its def line" in prompt
    assert "never open the block on the docstring" in prompt
    assert "Placeholders inject values" in prompt
    assert "never pasted into a sentence" in prompt
    assert "say what it says in your own words" in prompt
    # Coverage pass (post-Block-2 W4 rep 4): {{e3.text.QUANTITY_SPIKE_FACTOR}}
    # pathed into read_source's text for a constant.
    assert "never reaches inside a text field" in prompt
    assert "{{e3.text.SOME_NAME}} is not a path" in prompt


def test_drafter_resolves_with_the_configured_inline_limit(tool_pack):
    """The guard's one number reaches the resolver from HarnessSettings
    through the Drafter — no engine constant."""
    registry, ports = _evidence(
        tool_pack,
        [LLMResponse(content="Rows: {{e0.rows[0].column_name}}.", model="s")],
    )
    invocation = registry.invoke(
        "query_univariate_stats", {"table": "invoices", "column": "status"}
    )
    stub = ports.get(PortName.LLM)
    tight = Drafter(stub, "SYS", inline_value_max_chars=3)
    result = tight.draft("q", [invocation])
    assert result.resolution.misplaced == ["{{e0.rows[0].column_name}}"]
    shipped = tight.resolve(result.raw, [invocation], allow_passages_inline=True)
    assert shipped.text == "Rows: status."
