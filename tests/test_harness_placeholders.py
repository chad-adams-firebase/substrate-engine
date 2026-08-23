"""The {{eN.path}} resolver: value rendering, span tracking, and
failure reporting — the injected-figures half of §9.4."""

from engine.harness.placeholders import resolve_placeholders
from engine.ports.types import RunStatus
from engine.tools.envelope import (
    CheckExecutionOutput,
    RunSqlOutput,
    Table,
    ToolInvocation,
)


def _sql_evidence(rows, columns=None, total=None) -> list[ToolInvocation]:
    return [
        ToolInvocation(
            tool="run_sql",
            arguments={},
            status="ok",
            output=RunSqlOutput(
                sql="SELECT ...",
                table=Table(
                    columns=columns or (list(rows[0].keys()) if rows else []),
                    rows=rows,
                    total_row_count=total if total is not None else len(rows),
                ),
            ),
            substrates_read=[],
        )
    ]


def test_int_float_string_and_bool_render_for_prose():
    evidence = _sql_evidence(
        [{"n": 146, "rate": 0.15, "name": "RVX01", "flagged": True}]
    )
    resolution = resolve_placeholders(
        "Count {{e0.table.rows[0].n}}, rate {{e0.table.rows[0].rate}}, "
        "supplier {{e0.table.rows[0].name}}, flagged {{e0.table.rows[0].flagged}}.",
        evidence,
    )
    assert resolution.failures == []
    assert resolution.text == (
        "Count 146, rate 0.15, supplier RVX01, flagged true."
    )


def test_injected_spans_cover_exactly_the_injected_values():
    evidence = _sql_evidence([{"n": 146}])
    resolution = resolve_placeholders(
        "Of {{e0.table.rows[0].n}} invoices, {{e0.table.total_row_count}} row.",
        evidence,
    )
    values = [
        resolution.text[span.start : span.end]
        for span in resolution.injected_spans
    ]
    assert values == ["146", "1"]
    # Addendum N3: each span carries the evidence path it resolved
    # from — the Verifier's basis for verified-by-construction.
    assert [span.ref for span in resolution.injected_spans] == [
        "e0.table.rows[0].n",
        "e0.table.total_row_count",
    ]


def test_bad_index_bad_path_and_non_scalar_fail_visibly():
    evidence = _sql_evidence([{"n": 146}])
    resolution = resolve_placeholders(
        "A {{e9.table.rows[0].n}} B {{e0.table.rows[0].missing}} "
        "C {{e0.table.rows}} D {{e0.table.rows[5].n}}",
        evidence,
    )
    assert len(resolution.failures) == 4
    # Failed placeholders stay verbatim in the text for the retry.
    assert "{{e9.table.rows[0].n}}" in resolution.text
    assert resolution.injected_spans == []


def test_error_invocation_has_no_output_to_resolve_against():
    errored = ToolInvocation(
        tool="run_sql",
        arguments={},
        status="error",
        error="boom",
        substrates_read=[],
    )
    resolution = resolve_placeholders("{{e0.table.rows[0].n}}", [errored])
    assert resolution.failures == ["{{e0.table.rows[0].n}}"]


def test_run_status_paths_resolve():
    # The carryback's C1/C1b failures: the payload key was `status`,
    # colliding with the invocation-level status and inviting wrong
    # paths. Renamed run_status, a did_run answer is now addressable.
    evidence = [
        ToolInvocation(
            tool="check_execution",
            arguments={},
            status="ok",
            output=CheckExecutionOutput(
                run_status=RunStatus(
                    ran=True, count=16, detail="16 stale_sweep_completed events"
                )
            ),
            substrates_read=[],
        )
    ]
    resolution = resolve_placeholders(
        "Ran: {{e0.run_status.ran}}, count {{e0.run_status.count}}.",
        evidence,
    )
    assert resolution.failures == []
    assert resolution.text == "Ran: true, count 16."


def test_output_prefixed_paths_resolve_identically():
    # Addendum N1: render_evidence shows the drafter the tool result
    # nested under "output", so drafters write {{e0.output.path}} and
    # believed JSON beat the prompt's examples. Both spellings resolve.
    evidence = _sql_evidence([{"n": 146}])
    plain = resolve_placeholders("{{e0.table.rows[0].n}}", evidence)
    prefixed = resolve_placeholders("{{e0.output.table.rows[0].n}}", evidence)
    assert plain.failures == [] and prefixed.failures == []
    assert plain.text == prefixed.text == "146"
    assert plain.injected_spans == prefixed.injected_spans


def test_the_exact_c1b_placeholder_resolves():
    # Addendum N1: {{e1.output.run_status.count}} — right index, right
    # renamed field, right leaf, one spurious prefix — killed C1b.
    padding = _sql_evidence([{"n": 1}])
    evidence = padding + [
        ToolInvocation(
            tool="check_execution",
            arguments={},
            status="ok",
            output=CheckExecutionOutput(
                run_status=RunStatus(
                    ran=True, count=16, detail="16 stale_sweep_completed events"
                )
            ),
            substrates_read=[],
        )
    ]
    resolution = resolve_placeholders(
        "The sweep ran {{e1.output.run_status.count}} times.", evidence
    )
    assert resolution.failures == []
    assert resolution.text == "The sweep ran 16 times."


def test_bare_output_is_still_a_failure():
    # {{e0.output}} names the whole structure, not a scalar; the strip
    # applies only to an "output." prefix with a path behind it.
    evidence = _sql_evidence([{"n": 146}])
    resolution = resolve_placeholders("{{e0.output}}", evidence)
    assert resolution.failures == ["{{e0.output}}"]


def test_text_without_placeholders_passes_through_untouched():
    resolution = resolve_placeholders("No figures here.", [])
    assert resolution.text == "No figures here."
    assert resolution.failures == [] and resolution.injected_spans == []
