"""The {{eN.path}} resolver: value rendering, span tracking, and
failure reporting — the injected-figures half of §9.4."""

from engine.harness.placeholders import resolve_placeholders
from engine.tools.envelope import RunSqlOutput, Table, ToolInvocation


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
    values = [resolution.text[a:b] for a, b in resolution.injected_spans]
    assert values == ["146", "1"]


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


def test_text_without_placeholders_passes_through_untouched():
    resolution = resolve_placeholders("No figures here.", [])
    assert resolution.text == "No figures here."
    assert resolution.failures == [] and resolution.injected_spans == []
