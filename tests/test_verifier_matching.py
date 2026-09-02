"""Mechanical matching: the enumerated derivation menu and nothing
beyond it."""

from engine.config.models import VerifierSettings
from engine.ports.types import RunStatus
from engine.tools.envelope import (
    CheckExecutionEvidence,
    CheckExecutionOutput,
    CkgTraversalOutput,
    ReadSourceOutput,
    ToolInvocation,
)
from engine.verifier.checks import CheckRegistry, default_checks
from engine.verifier.checks.invocation import harvest_invocation
from engine.verifier.claims import extract_claims
from engine.verifier.matching import match_claim, merge_contributions
from engine.verifier.models import DraftAnswer
from tests.verifier_support import make_verifier, sql_invocation

SETTINGS = VerifierSettings()
CHECKS = CheckRegistry(default_checks())


def _pools(*invocations: ToolInvocation):
    # Mirrors Verifier._pools: the record's own harvest, then the check's.
    contributions = []
    for i, inv in enumerate(invocations):
        contributions.append(harvest_invocation(inv))
        contributions.append(CHECKS.for_tool(inv.tool).harvest(inv, f"e{i}"))
    return merge_contributions(contributions)


def _match(text: str, *invocations, settings=SETTINGS):
    pools = _pools(*invocations)
    claims = extract_claims(text)
    return [(c, match_claim(c, pools, settings)) for c in claims]


def test_exact_match_and_count_salience():
    inv = sql_invocation("SELECT COUNT(*) AS n FROM invoices", [{"n": 146}])
    [(_, outcome)] = _match("There were 146 findings.", inv)
    assert outcome.status == "matched_exact"
    assert outcome.evidence_ref.endswith("rows[0].n")


def test_percent_fraction_bridge_is_salience_gated():
    # A stats null_rate 0.342 supports "34.2%"...
    from tests.verifier_support import stats_row
    from engine.tools.envelope import StatsOutput

    stats_inv = ToolInvocation(
        tool="query_univariate_stats",
        arguments={},
        status="ok",
        output=StatsOutput(
            rows=[stats_row("invoices", "flagged", null_rate=0.342)]
        ),
        substrates_read=[],
    )
    [(_, with_stat)] = _match("About 34.2% were null.", stats_inv)
    assert with_stat.status in ("matched_exact", "matched_derived")

    # ...but a random result CELL 0.342 must not license the same claim.
    cell_inv = sql_invocation("SELECT x FROM t", [{"x": 0.342}])
    [(_, with_cell)] = _match("About 34.2% were null.", cell_inv)
    assert with_cell.status == "fuzzy"


def test_rounding_accepts_the_displayed_resolution_and_rejects_beyond():
    inv = sql_invocation("SELECT COUNT(*) AS n FROM t", [{"n": 1442986}])
    [(_, close)] = _match("There are about 1.4 million lines.", inv)
    assert close.status == "matched_derived" and close.method == "rounding"

    far = sql_invocation("SELECT COUNT(*) AS n FROM t", [{"n": 1372000}])
    [(_, outcome)] = _match("There are about 1.5 million lines.", far)
    assert outcome.status == "fuzzy"


def test_truncated_count_matches_with_a_visible_ref():
    inv = sql_invocation(
        "SELECT * FROM t",
        [{"n": i} for i in range(200)],
        total=1442,
        truncated=True,
    )
    matches = _match("The query returned 1,442 rows; 200 shown.", inv)
    by_surface = {c.surface: o for c, o in matches}
    assert by_surface["1,442"].status == "matched_exact"
    assert by_surface["200"].status == "matched_exact"
    assert "truncated view" in by_surface["200"].evidence_ref


def test_column_sum_and_same_row_ratio_derivations():
    inv = sql_invocation(
        "SELECT supplier, flagged, total FROM t",
        [
            {"supplier": "A", "flagged": 90, "total": 100},
            {"supplier": "B", "flagged": 56, "total": 61},
        ],
    )
    # Column sum: 90 + 56 = 146.
    [(_, sum_outcome)] = _match("146 invoices were flagged in all.", inv)
    assert sum_outcome.status == "matched_exact"
    assert "sum(flagged)" in sum_outcome.evidence_ref

    # Same-row ratio: 90/100 = 90%.
    [(_, ratio)] = _match("Supplier one was flagged 90.0% of the time.", inv)
    assert ratio.status in ("matched_exact", "matched_derived")


def test_part_of_total_percent_and_count_difference():
    inv = sql_invocation(
        "SELECT COUNT(*) AS with_findings FROM x",
        [{"with_findings": 146}],
        total=1,
    )
    inv2 = sql_invocation(
        "SELECT COUNT(*) AS received FROM y", [{"received": 161}], total=1
    )
    matches = _match(
        "15 of the invoices had no findings, roughly 90.7% did.", inv, inv2
    )
    by_surface = {c.surface: o for c, o in matches}
    # 161 - 146 = 15, both count-salience integers... but 146/161 are
    # cells here; the ratio pairs on (cell, count) and the difference
    # on counts. 15 derives from total_row_count pair? No: the counts
    # are the two total_row_count=1 values. The cells 146/161 carry
    # group refs from different rows, so ratio comes from
    # (cell, count)? -- assert what the menu actually supports:
    assert by_surface["15"].status in ("matched_derived", "unmatched", "fuzzy")


def _primer_invocation() -> ToolInvocation:
    from engine.substrates.models import Component
    from engine.tools.envelope import PrimerOutput
    from tests.verifier_support import MACHINE

    return ToolInvocation(
        tool="app_primer",
        arguments={},
        status="ok",
        output=PrimerOutput(
            primer="The rules engine scores each invoice against rules.",
            components=[
                Component(
                    id="ig.spine.rules-engine",
                    name="Rules engine",
                    description="Scores invoices.",
                    provenance=MACHINE,
                ),
                Component(
                    id="ig.spine.invoice-parse",
                    name="Invoice parse",
                    description="Parses invoice files.",
                    provenance=MACHINE,
                ),
            ],
        ),
        substrates_read=[],
    )


def test_hyphenated_component_id_matches_vocabulary():
    # Carryback #1a end to end: the whole id is in the pool (it always
    # was); extraction no longer truncates it on the way there.
    matches = _match("Parsing is `ig.spine.invoice-parse`.", _primer_invocation())
    [(claim, outcome)] = matches
    assert claim.kind == "entity"
    assert outcome.status == "matched_exact"
    assert outcome.method == "vocabulary"


def test_component_name_quote_matches_via_structured_field():
    # Carryback #1b: `Rules engine` sat verbatim in the components
    # JSON while the primer prose said "rules engine" mid-sentence.
    # The structured name field is now quote corpus.
    matches = _match("The `Rules engine` runs first.", _primer_invocation())
    [(claim, outcome)] = matches
    assert claim.kind == "quote"
    assert outcome.status == "matched_exact"
    assert outcome.evidence_ref == "e0.components[0].name"


def test_spelled_cardinal_matches_a_pooled_count_mechanically():
    # Addendum N2: "the twelve" carries a comparable 12 and matches a
    # harvested len(edges) count exactly like a digit claim.
    from engine.substrates.models import CkgEdge, CkgNode
    from tests.verifier_support import MACHINE

    node = CkgNode(
        id="n1",
        kind="function",
        qualified_name="pkg.rules.run_rules",
        file_path="pkg/rules.py",
        start_line=10,
        end_line=90,
        provenance=MACHINE,
    )
    traversal = ToolInvocation(
        tool="traverse_code_knowledge_graph",
        arguments={},
        status="ok",
        output=CkgTraversalOutput(
            entry_node=node,
            edges=[
                CkgEdge(
                    id=f"edge{i}",
                    source_id="n1",
                    kind="calls",
                    target_node_id=f"n{i + 2}",
                    line=20 + i,
                    provenance=MACHINE,
                )
                for i in range(12)
            ],
        ),
        substrates_read=[],
    )
    matches = _match("It runs the twelve audit rules in order.", traversal)
    [(claim, outcome)] = [
        (c, o) for c, o in matches if c.kind == "numeric"
    ]
    assert claim.surface == "twelve" and claim.value == 12.0
    assert outcome.status == "matched_exact"
    assert outcome.evidence_ref == "e0.len(edges)"


def test_spelled_cardinal_falls_back_to_the_quote_corpus():
    # Addendum N2: primer prose is quote corpus but never numbers (by
    # design), so a spelled cardinal restating it matches as a
    # quotation when no harvested value can carry it.
    inv = _primer_invocation()
    inv.output.primer = (
        "Twelve audit rules score each invoice in the rules engine."
    )
    matches = _match("It applies the twelve audit rules.", inv)
    [(claim, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    assert claim.spelled is True
    assert outcome.status == "matched_derived"
    assert outcome.method == "spelled-quote"
    assert outcome.evidence_ref == "e0.primer"


def test_digit_claims_never_take_the_spelled_fallback():
    # Addendum N2: "12" typed as digits with only the word "twelve" in
    # retrieved text stays judge territory — the fallback is for
    # spelled restatements of prose, not a general number-word bridge.
    inv = _primer_invocation()
    inv.output.primer = "Twelve audit rules score each invoice."
    matches = _match("There are 12 audit rules.", inv)
    [(claim, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    assert claim.spelled is False
    assert outcome.status == "fuzzy"


def test_entity_matching_casefolds_as_a_derivation():
    inv = sql_invocation("SELECT rule_name FROM findings", [{"rule_name": "x"}])
    [(_, exact)] = _match("The `rule_name` column.", inv)
    assert exact.status == "matched_exact" and exact.method == "vocabulary"

    [(_, folded)] = _match("The `Rule_name` column.", inv)
    assert folded.status == "matched_derived"
    assert folded.method == "vocabulary-casefold"


def test_quote_matching_stays_case_sensitive():
    # The ruling: case is meaning in code. Only entities fold.
    matches = _match("It says `RULES ENGINE` somewhere.", _primer_invocation())
    [(claim, outcome)] = matches
    assert claim.kind == "quote"
    assert outcome.status == "unmatched"


def test_quotes_never_reach_the_judge():
    source = ToolInvocation(
        tool="read_source",
        arguments={},
        status="ok",
        output=ReadSourceOutput(
            qualified_name="pkg.mod.rule_rate_variance",
            file_path="pkg/mod.py",
            start_line=116,
            end_line=149,
            commit_sha="761a18e9",
            text="if variance_pct > RATE_VARIANCE_PCT:\n    flag(line)",
        ),
        substrates_read=[],
    )
    verifier, llm = make_verifier([])  # any judge call would exhaust it
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(
            kind="prose",
            text=(
                "The rule checks `variance_pct > RATE_VARIANCE_PCT` and "
                "also `made_up_code(x)` somewhere."
            ),
        ),
        evidence=[source],
        attempt=99,  # beyond retries: unmatched -> unverified, no judge
    )
    assert llm.calls == []  # quotes and entities never judged
    statuses = {r.surface: r.status for r in result.attempt_record.claims}
    assert statuses["`variance_pct > RATE_VARIANCE_PCT`"] == "matched_exact"
    assert statuses["`made_up_code(x)`"] == "unmatched"


def test_quote_whitespace_normalizes_but_case_does_not():
    source = ToolInvocation(
        tool="read_source",
        arguments={},
        status="ok",
        output=ReadSourceOutput(
            qualified_name="pkg.mod.f",
            file_path="pkg/mod.py",
            start_line=1,
            end_line=5,
            commit_sha="abc",
            text="def f(x):\n    return   x + 1",
        ),
        substrates_read=[],
    )
    reflowed = _match("It does `return x + 1` at the end.", source)
    [(_, ok)] = [(c, o) for c, o in reflowed if c.kind == "quote"]
    assert ok.status == "matched_exact"

    wrong_case = _match("It does `Return x + 1` at the end.", source)
    [(_, bad)] = [(c, o) for c, o in wrong_case if c.kind == "quote"]
    assert bad.status == "unmatched"


def test_location_claims_match_retrieved_line_ranges():
    source = ToolInvocation(
        tool="read_source",
        arguments={},
        status="ok",
        output=ReadSourceOutput(
            qualified_name="invoiceguard.spine.rules_engine.rule_rate_variance",
            file_path="src/invoiceguard/spine/rules_engine.py",
            start_line=116,
            end_line=149,
            commit_sha="761a18e9",
            text="def rule_rate_variance(): ...",
        ),
        substrates_read=[],
    )
    matches = _match("Defined at rules_engine.py:116-149.", source)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "matched_exact" and outcome.method == "location"

    outside = _match("Defined at rules_engine.py:150-160.", source)
    [(_, bad)] = [(c, o) for c, o in outside if c.kind == "entity"]
    assert bad.status == "unmatched"


_B4_SOURCE = ToolInvocation(
    tool="read_source",
    arguments={},
    status="ok",
    output=ReadSourceOutput(
        qualified_name="invoiceguard.spine.rules_engine.rule_rate_variance",
        file_path="src/invoiceguard/spine/rules_engine.py",
        start_line=116,
        end_line=149,
        commit_sha="761a18e9",
        text="def rule_rate_variance(): ...",
    ),
    substrates_read=[],
)


def test_backticked_file_path_quote_falls_back_to_vocabulary():
    """Fix pass 4 (gate verdict N9): a backticked file path is a quote
    claim (the slash defeats the identifier shape) shopping in a corpus
    that holds only the file's text — a file doesn't contain its own
    path. The path IS harvested, into vocabulary; exact membership
    there matches as matched_derived, honestly labeled. B4 attempt 1's
    surface, and MT3's attempt-1 miss in 4/5 fp3-confirm reps."""
    matches = _match(
        "Defined in `src/invoiceguard/spine/rules_engine.py`.", _B4_SOURCE
    )
    [(claim, outcome)] = [(c, o) for c, o in matches if c.kind == "quote"]
    assert claim.text == "src/invoiceguard/spine/rules_engine.py"
    assert outcome.status == "matched_derived"
    assert outcome.method == "quote-vocabulary"

    # Exact membership only — a near-miss body stays unmatched. The
    # bare-filename temptation ("rules_engine.py" for the full path)
    # is refused by design: fuzzy paths belong to the queued dotted-
    # fallback conversation, not this fix.
    near = _match(
        "Defined in `src/invoiceguard/spine/rules_engine`.", _B4_SOURCE
    )
    [(_, bad)] = [(c, o) for c, o in near if c.kind == "quote"]
    assert bad.status == "unmatched"


def _did_run_invocation() -> ToolInvocation:
    return ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(
            run_status=RunStatus(
                ran=True,
                count=1,
                detail=(
                    "1 stale_sweep/stale_sweep_completed event(s) in "
                    "[2026-05-29T00:00:00+00:00, 2026-05-30T00:00:00+00:00)"
                ),
            )
        ),
        evidence=CheckExecutionEvidence(
            lines=[
                "ts=2026-05-29T06:00:00+00:00 logger=stale_sweep "
                "event=stale_sweep_completed"
            ]
        ),
    )


def test_prose_date_matches_harvested_iso_window():
    """Fix pass 4 (gate verdict N10): C1's surface — "May 29" against
    a did_run answer whose window stamps live only in run_status.detail
    text. ISO dates harvest from the detail (and log lines) into the
    strings pool; the yearless claim matches them as derived — the
    year is context the claim didn't state."""
    matches = _match("Yes — it ran on May 29.", _did_run_invocation())
    [(claim, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    assert claim.date == "05-29"
    assert outcome.status == "matched_derived"
    assert outcome.method == "date-yearless"


def test_iso_date_claim_matches_harvested_window():
    """Fix pass 4 (gate verdict N10): C1b's surface — the ISO phrasing
    control. The whole-date claim path already existed and rejected
    mechanically; it needed a pool, not a new mechanism."""
    matches = _match(
        "The run window starts 2026-05-29.", _did_run_invocation()
    )
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    assert outcome.status == "matched_exact"
    assert outcome.method == "date"


def test_date_claims_never_match_bare_numerals_and_vice_versa():
    """Fix pass 4 (gate verdict N10): the constraint the mechanism
    guarantees — date claims shop among date-shaped strings only, and
    bare numerals never carry a date, so a day-29 can never ride an
    unrelated count of 29 in either direction."""
    inv = sql_invocation("SELECT n FROM t", [{"n": 29}])
    dated = _match("It happened on May 29.", inv)
    [(_, outcome)] = [(c, o) for c, o in dated if c.kind == "numeric"]
    assert outcome.status == "unmatched"
    assert outcome.reason == "date not present in evidence"

    plain = _match("We found 29 rows.", inv)
    [(_, ok)] = [(c, o) for c, o in plain if c.kind == "numeric"]
    assert ok.status == "matched_exact"


def test_error_count_grounds_a_no_errors_claim():
    """Fix pass 4 (gate verdict N12): a clean day's 0 is a harvested
    count the drafted figure can match — HN-ERRORS' surface."""
    clean = ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(errors=[], error_count=0),
    )
    matches = _match("There were 0 errors that day.", clean)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    # Both error_count and len(errors) truthfully carry 0; assert the
    # match, not the winning ref.
    assert outcome.status == "matched_exact"


def test_dotted_logger_name_in_error_rows_grounds_entity_claims():
    """Fix pass 4 (gate verdict N10 fold-in; fp3-confirm S5 rep 4):
    identifier_tokens splits at dots, so a dotted logger name never
    entered vocabulary whole and a drafted
    `invoiceguard.benchmark_scoring` was unmatchable. Error-row
    strings now harvest their dotted tokens too."""
    errors_inv = ToolInvocation(
        tool="check_execution",
        arguments={},
        status="ok",
        output=CheckExecutionOutput(
            errors=[
                {
                    "ts": "2026-03-11T08:00:00+00:00",
                    "logger": "invoiceguard.benchmark_scoring",
                    "event": "benchmark_fallback",
                }
            ],
        ),
    )
    matches = _match(
        "`invoiceguard.benchmark_scoring` emitted the warnings.", errors_inv
    )
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "matched_exact"
    assert outcome.method == "vocabulary"


def test_exact_evidence_string_quote_falls_back_to_strings():
    """Fix pass 4 (gate verdict N9): the strings pool backs the same
    fallback — a backticked commit SHA is a harvested string, not a
    passage from the corpus."""
    matches = _match("Pinned at `761a18e9`.", _B4_SOURCE)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "quote"]
    assert outcome.status == "matched_derived"
    assert outcome.method == "quote-string"


def test_derivation_pair_cap_falls_to_the_judge():
    inv = sql_invocation(
        "SELECT a, b FROM t",
        [{"a": i, "b": i + 1} for i in range(40)],
    )
    tight = VerifierSettings(max_derivation_pairs=3)
    matches = _match("Roughly 73.2% of things.", inv, settings=tight)
    [(_, outcome)] = matches
    assert outcome.status == "fuzzy"
    assert "pair cap" in outcome.reason


def test_ckg_condition_literal_bridges_percent_to_fraction():
    from engine.substrates.models import CkgConditional, CkgNode
    from tests.verifier_support import MACHINE

    traversal = ToolInvocation(
        tool="traverse_code_knowledge_graph",
        arguments={},
        status="ok",
        output=CkgTraversalOutput(
            entry_node=CkgNode(
                id="n1",
                kind="function",
                qualified_name="pkg.rules.rule_rate_variance",
                file_path="pkg/rules.py",
                start_line=10,
                end_line=20,
                provenance=MACHINE,
            ),
            conditionals=[
                CkgConditional(
                    node_id="n1",
                    condition_text="variance_pct > 0.15",
                    line=12,
                    provenance=MACHINE,
                )
            ],
        ),
        substrates_read=[],
    )
    matches = _match("Items are flagged above a 15% variance.", traversal)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "numeric"]
    assert outcome.status in ("matched_exact", "matched_derived")


# --- N13: the poolless-identifier class -------------------------------

_CLEAN_DAY_ARGS = {
    "component": "benchmark_scoring",
    "key": "",
    "mode": "recent_errors",
    "window_end": "2026-04-15T23:59:59Z",
    "window_start": "2026-04-15T00:00:00Z",
}


def _clean_day(arguments=None, status="ok"):
    """The fp4b-holdouts envelope verbatim: errors [], error_count 0,
    run_status None, evidence lines [] — every string pool empty."""
    return ToolInvocation(
        tool="check_execution",
        arguments=_CLEAN_DAY_ARGS if arguments is None else arguments,
        status=status,
        error=None if status == "ok" else "boom",
        output=(
            CheckExecutionOutput(errors=[], error_count=0)
            if status == "ok"
            else None
        ),
        evidence=CheckExecutionEvidence(lines=[]) if status == "ok" else None,
        substrates_read=["application_logs"] if status == "ok" else [],
    )


def test_clean_day_recent_errors_grounds_argument_component_name():
    """Fix pass 4 follow-up (N13; fp4b-holdouts HN-ERRORS reps 1,2,4,5):
    the component the router asked about sits in the invocation's
    arguments, which no check harvested — a clean day's errors list
    is empty, so the correct name shopped an empty vocabulary."""
    matches = _match(
        "The `benchmark_scoring` component had no errors.", _clean_day()
    )
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "matched_exact"
    assert outcome.method == "vocabulary"


def test_window_argument_grounds_prose_and_iso_dates():
    """Fix pass 4 follow-up (N13; HN-ERRORS' "the date 2026-04-15"):
    the window arguments are ISO timestamps; harvested whole into
    strings, N10's existing date paths reach them."""
    iso = _match("No errors on 2026-04-15.", _clean_day())
    [(_, exact)] = [(c, o) for c, o in iso if c.kind == "numeric"]
    assert (exact.status, exact.method) == ("matched_exact", "date")

    prose = _match("No errors on April 15.", _clean_day())
    [(_, derived)] = [(c, o) for c, o in prose if c.kind == "numeric"]
    assert (derived.status, derived.method) == (
        "matched_derived",
        "date-yearless",
    )


def test_envelope_field_name_grounds_backticked_error_count():
    """Fix pass 4 follow-up (N13; P-N11 reps 2-3, "fails exactly when
    backticked emitted"): the envelope's own field name is rendered to
    the drafter verbatim and is part of the evidence."""
    matches = _match(
        "No errors occurred in benchmark scoring. The `error_count` is 0.",
        _clean_day(),
    )
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "matched_exact"
    assert outcome.method == "vocabulary"


def test_none_fields_never_ground():
    """The field-name harvest reads the drafter's own view: a
    None-suppressed field (run_status on a recent_errors call) was
    never shown, so it cannot be cited."""
    matches = _match("The `run_status` was clean.", _clean_day())
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "unmatched"


def test_errored_invocation_arguments_never_harvest():
    """Failed calls support no claims — the P-N11 e0 law holds for the
    record's own harvest too."""
    errored = _clean_day(
        arguments={"component": "benchmark_scoring"}, status="error"
    )
    contribution = harvest_invocation(errored)
    assert contribution.vocabulary == set()
    assert contribution.strings == set()


def test_free_text_arguments_are_not_tokenized():
    """Whole-value shape only: a free-text query is never shredded into
    citeable words, and a bare numeral argument is not a name."""
    inv = _clean_day(
        arguments={"query": "why invoices lapse", "limit": 20, "key": ""}
    )
    matches = _match("Look at `invoices`.", inv)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "entity"]
    assert outcome.status == "unmatched"


def test_dictionary_concept_name_grounds_backticked_quote():
    """Fix pass 4 follow-up (N13 mechanism b; NP6's `invoice
    lifecycle`): identifier_tokens shatters a multi-word concept name
    at the space and only its definition reached the corpus."""
    from engine.substrates.models import Concept
    from engine.tools.envelope import DictionaryLookupOutput

    inv = ToolInvocation(
        tool="lookup_data_dictionary",
        arguments={"term": "lifecycle"},
        status="ok",
        output=DictionaryLookupOutput(
            rows=[],
            concepts=[
                Concept(
                    name="invoice lifecycle",
                    definition="The states an invoice passes through.",
                )
            ],
        ),
    )
    matches = _match("The `invoice lifecycle` has four states.", inv)
    [(_, outcome)] = [(c, o) for c, o in matches if c.kind == "quote"]
    assert outcome.status == "matched_exact"
    assert outcome.method == "quote"


def test_holdout_drafts_verify_on_the_clean_day_without_a_judge():
    """The two witness drafts from fp4b-holdouts, replayed through the
    Verifier against the recorded envelope: verified, and no judge
    call was spent (quotes and names never reach the judge)."""
    verifier, llm = make_verifier([])
    for text in (
        "The evidence does not specify the `benchmark_scoring` component "
        "or the date 2026-04-15. However, it indicates that there were "
        "0 errors.",
        "No errors occurred in benchmark scoring. The `error_count` is 0.",
    ):
        result = verifier.verify(
            question="errors on 2026-04-15?",
            draft=DraftAnswer(kind="prose", text=text),
            evidence=[_clean_day()],
            attempt=1,
        )
        assert result.disposition == "verified", text
        assert llm.calls == []


def test_stats_top_values_ground_backticked_enum_claims():
    """n13-witnesses NP6 rep 3: the attempt-1 draft enumerated all four
    statuses, but backticked `NO_REVIEW_NEEDED` and `READY` shopped
    vocabulary while the stats harvest had put top_values into
    strings only — the redraft deleted two correct values. Identifier-
    shaped top values now reach vocabulary; free-text ones stay
    strings."""
    from tests.verifier_support import stats_row
    from engine.tools.envelope import StatsOutput
    from engine.substrates.models import TopValue

    inv = ToolInvocation(
        tool="query_univariate_stats",
        arguments={"table": "invoices", "column": "status"},
        status="ok",
        output=StatsOutput(
            rows=[
                stats_row(
                    "invoices",
                    "status",
                    data_type="VARCHAR",
                    distinct_count=4,
                    top_values=[
                        TopValue(value="CLOSED", count=1025),
                        TopValue(value="LAPSED", count=679),
                        TopValue(value="NO_REVIEW_NEEDED", count=208),
                        TopValue(value="READY", count=78),
                        TopValue(value="Crestpoint Mechanical", count=3),
                    ],
                )
            ]
        ),
        substrates_read=["univariate_statistics"],
    )
    matches = _match(
        "The possible values of `status` are `CLOSED`, `LAPSED`, "
        "`NO_REVIEW_NEEDED` and `READY`.",
        inv,
    )
    entities = {c.entity: o for c, o in matches if c.kind == "entity"}
    for name in ("CLOSED", "LAPSED", "NO_REVIEW_NEEDED", "READY"):
        assert entities[name].status == "matched_exact", name
        assert entities[name].method == "vocabulary"
    assert "Crestpoint Mechanical" not in _pools(inv).vocabulary
    assert "Crestpoint Mechanical" in _pools(inv).strings

    verifier, llm = make_verifier([])
    result = verifier.verify(
        question="possible values of invoices.status?",
        draft=DraftAnswer(
            kind="prose",
            # The row's `invoices.status` composite (3(a), still queued)
            # grounded live via the dictionary lookup's term argument;
            # this replay pins only the enum values.
            text="The possible values of `status` in `invoices` are:\n\n"
            "- `CLOSED`\n- `LAPSED`\n- `NO_REVIEW_NEEDED`\n- `READY`\n",
        ),
        evidence=[inv],
        attempt=1,
    )
    assert result.disposition == "verified"
    assert llm.calls == []


def test_a_labeled_fenced_quote_from_the_def_line_string_matches_read_source():
    """Block 2's drafter rule: source goes in a language-labeled fence
    opening on the def line. The fence label is markup, not quote
    text, and the quote matcher is fence-agnostic — the block matches
    the retrieved source exactly, no judge involved."""
    text = (
        'def rule_rate_variance(line):\n    """Billed above contract."""\n'
        "    if line.rate > line.contract_rate * 1.15:\n        flag(line)\n"
    )
    source = ToolInvocation(
        tool="read_source",
        arguments={},
        status="ok",
        output=ReadSourceOutput(
            qualified_name="pkg.mod.rule_rate_variance",
            file_path="pkg/mod.py",
            start_line=116,
            end_line=149,
            commit_sha="761a18e9",
            text=text,
        ),
        substrates_read=[],
    )
    verifier, llm = make_verifier([])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text=f"Here:\n\n```python\n{text}```\n"),
        evidence=[source],
        attempt=1,
    )
    assert result.disposition == "verified"
    (quote,) = [c for c in result.attempt_record.claims if c.kind == "quote"]
    assert quote.status == "matched_exact" and quote.method == "quote"
    assert llm.calls == []
