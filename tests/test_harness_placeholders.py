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


def test_error_count_paths_resolve():
    """Fix pass 4 (gate verdict N12): the clean day is now sayable —
    error_count is a walkable scalar where errors: [] never was, in
    both placeholder spellings (the run_status precedent)."""
    evidence = [
        ToolInvocation(
            tool="check_execution",
            arguments={},
            status="ok",
            output=CheckExecutionOutput(errors=[], error_count=0),
            substrates_read=[],
        )
    ]
    plain = resolve_placeholders("{{e0.error_count}} errors.", evidence)
    prefixed = resolve_placeholders(
        "{{e0.output.error_count}} errors.", evidence
    )
    assert plain.failures == [] and prefixed.failures == []
    assert plain.text == prefixed.text == "0 errors."


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


def test_money_hinted_cells_inject_as_currency():
    """§10.5 / NP3: the drafter never types the figure, and the figure
    code injects reads as currency when the column says money — no
    float tail in prose, exactly as in the table."""
    from engine.tools.envelope import ColumnFormat

    evidence = _sql_evidence(
        [{"n": 78, "total_opportunity": 8308.92139244107}],
    )
    evidence[0].output.table.column_formats = {
        "total_opportunity": ColumnFormat(kind="money", symbol="$")
    }
    resolution = resolve_placeholders(
        "{{e0.table.rows[0].n}} items worth "
        "{{e0.table.rows[0].total_opportunity}} "
        "({{e0.output.table.rows[0].total_opportunity}}).",
        evidence,
    )
    assert resolution.failures == []
    assert resolution.text == "78 items worth $8,308.92 ($8,308.92)."
    values = [
        resolution.text[span.start : span.end]
        for span in resolution.injected_spans
    ]
    assert values == ["78", "$8,308.92", "$8,308.92"]


def test_unhinted_float_cells_still_render_round_trip():
    evidence = _sql_evidence([{"total_opportunity": 8308.92139244107}])
    resolution = resolve_placeholders(
        "{{e0.table.rows[0].total_opportunity}}", evidence
    )
    assert resolution.text == "8308.92139244107"


def test_duration_hinted_cells_inject_humanized():
    """Block 2: the play session's "1.0806402437502474 days" in prose
    — the figure is code-injected, so the same hint that formats the
    table formats the sentence."""
    from engine.tools.envelope import ColumnFormat

    evidence = _sql_evidence([{"avg_days": 1.0806402437502474, "wait": "1:00:00"}])
    evidence[0].output.table.column_formats = {
        "avg_days": ColumnFormat(kind="duration", unit="days"),
        "wait": ColumnFormat(kind="duration"),
    }
    resolution = resolve_placeholders(
        "On average {{e0.table.rows[0].avg_days}}, or {{e0.table.rows[0].wait}} at best.",
        evidence,
    )
    assert resolution.failures == []
    assert resolution.text == "On average 1.1 days, or 1 hour at best."


# --- Values, not passages (Phase 5 Block 2's text-block guard) --------

SNIPPET = ("Scores each received invoice against the twelve audit rules and " * 4)[:240]
SOURCE = "def rule_rate_variance(line):\n    if line.rate > contract:\n        flag(line)"


def _passage_evidence():
    from engine.tools.envelope import DocSearchHit, DocSearchOutput, ReadSourceOutput

    return [
        ToolInvocation(
            tool="search_business_docs",
            arguments={},
            status="ok",
            output=DocSearchOutput(
                hits=[DocSearchHit(slug="s", title="Scoring", heading="h", snippet=SNIPPET, score=3)]
            ),
            substrates_read=[],
        ),
        ToolInvocation(
            tool="read_source",
            arguments={},
            status="ok",
            output=ReadSourceOutput(
                qualified_name="pkg.rule_rate_variance",
                file_path="pkg.py",
                start_line=1,
                end_line=3,
                commit_sha="abc",
                text=SOURCE,
            ),
            substrates_read=[],
        ),
    ]


def test_a_passage_mid_sentence_is_misplaced_not_injected():
    resolution = resolve_placeholders(
        "Scoring works like this: {{e0.hits[0].snippet}} and the code is "
        "{{e1.text}} which flags lines; see {{e0.hits[0].title}}.",
        _passage_evidence(),
        inline_value_max_chars=120,
    )
    assert resolution.failures == []
    assert resolution.misplaced == ["{{e0.hits[0].snippet}}", "{{e1.text}}"]
    # Verbatim, like a failure, so the retry sees what it wrote; the
    # short value still injects.
    assert "{{e0.hits[0].snippet}}" in resolution.text
    assert "{{e1.text}}" in resolution.text
    assert "see Scoring." in resolution.text
    assert [span.ref for span in resolution.injected_spans] == ["e0.hits[0].title"]


def test_a_passage_inside_a_fenced_code_block_resolves():
    text = "Here is the rule:\n```python\n{{e1.text}}\n```\nIt flags lines."
    resolution = resolve_placeholders(
        text, _passage_evidence(), inline_value_max_chars=120
    )
    assert resolution.misplaced == [] and resolution.failures == []
    assert "```python\ndef rule_rate_variance(line):" in resolution.text
    (span,) = resolution.injected_spans
    assert resolution.text[span.start : span.end] == SOURCE


def test_an_unclosed_fence_still_counts_as_fenced():
    resolution = resolve_placeholders(
        "```python\n{{e1.text}}", _passage_evidence(), inline_value_max_chars=120
    )
    assert resolution.misplaced == []


def test_the_limit_is_the_line_length_and_multi_line_is_always_a_passage():
    evidence = _passage_evidence()
    # A 240-char snippet clears a generous limit; the source never does.
    generous = resolve_placeholders(
        "A: {{e0.hits[0].snippet}} B: {{e1.text}}", evidence, inline_value_max_chars=1000
    )
    assert generous.misplaced == ["{{e1.text}}"]
    # No limit (the resolver's unit-test default) means no guard at all.
    unguarded = resolve_placeholders("A: {{e1.text}}", evidence)
    assert unguarded.misplaced == [] and SOURCE in unguarded.text


def test_exhaustion_path_ships_the_passage_as_written():
    resolution = resolve_placeholders(
        "Code: {{e1.text}} end.",
        _passage_evidence(),
        inline_value_max_chars=120,
        allow_passages_inline=True,
    )
    assert resolution.misplaced == []
    assert resolution.text == f"Code: {SOURCE} end."
    assert len(resolution.injected_spans) == 1
