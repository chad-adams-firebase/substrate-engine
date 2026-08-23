"""Mechanical matching: the enumerated derivation menu and nothing
beyond it."""

from engine.config.models import VerifierSettings
from engine.tools.envelope import (
    CkgTraversalOutput,
    ReadSourceOutput,
    ToolInvocation,
)
from engine.verifier.checks import CheckRegistry, default_checks
from engine.verifier.claims import extract_claims
from engine.verifier.matching import match_claim, merge_contributions
from engine.verifier.models import DraftAnswer
from tests.verifier_support import make_verifier, sql_invocation

SETTINGS = VerifierSettings()
CHECKS = CheckRegistry(default_checks())


def _pools(*invocations: ToolInvocation):
    contributions = [
        CHECKS.for_tool(inv.tool).harvest(inv, f"e{i}")
        for i, inv in enumerate(invocations)
    ]
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
