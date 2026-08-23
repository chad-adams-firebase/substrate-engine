"""The verdict ladder, including phasing acceptance test (a): a
corrupted draft is caught, retried with feedback, and labeled
unverified — never shipped confident-but-unchecked."""

from engine.config.models import VerifierSettings
from engine.ports.types import LLMResponse
from engine.verifier.models import DraftAnswer, InjectedSpan
from engine.verifier.verdict import finalize, render_feedback
from tests.verifier_support import make_verifier, sql_invocation

LAST_WEEK_SQL = (
    "SELECT COUNT(*) AS invoice_count FROM invoices "
    "WHERE received_at BETWEEN '2026-05-23' AND '2026-05-29'"
)
EVIDENCE = [sql_invocation(LAST_WEEK_SQL, [{"invoice_count": 146}])]


def test_clean_draft_verifies_in_one_attempt():
    verifier, llm = make_verifier([])
    result = verifier.verify(
        question="how many?",
        draft=DraftAnswer(
            kind="prose", text="Last week, 146 invoices had findings."
        ),
        evidence=EVIDENCE,
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.attempt_record.unmatched_count == 0
    assert llm.calls == []  # nothing needed the judge


def test_acceptance_a_corrupted_draft_caught_retried_then_unverified():
    """Phasing Phase 4 done-check: wrong number -> caught -> retried
    -> labeled unverified."""
    judge_no = LLMResponse(
        content="NO — 14,600 is not among the evidence values.", model="s"
    )
    verifier, llm = make_verifier([judge_no, judge_no])

    first = verifier.verify(
        question="how many?",
        draft=DraftAnswer(
            kind="prose", text="Last week there were 14,600 invoices."
        ),
        evidence=EVIDENCE,
        attempt=1,
    )
    assert first.disposition == "retry"
    assert first.judge_calls == 1
    (item,) = first.feedback.items
    assert item.surface == "14,600"
    assert any("146" in n for n in item.nearest_evidence)
    rendered = render_feedback(first.feedback)
    assert "not supported" in rendered[0] and "146" in rendered[0]

    # The redraft is still corrupted -> retries exhausted -> unverified.
    second = verifier.verify(
        question="how many?",
        draft=DraftAnswer(
            kind="prose", text="About 14,600 invoices had findings."
        ),
        evidence=EVIDENCE,
        attempt=2,
    )
    assert second.disposition == "unverified"

    verdict = finalize(
        attempts=[first.attempt_record, second.attempt_record],
        plausibility=second.plausibility,
        mode="prose",
        judge_calls=first.judge_calls + second.judge_calls,
        disposition="unverified",
    )
    assert verdict.disposition == "unverified"
    assert len(verdict.attempts) == 2
    assert verdict.judge_calls == 2
    unmatched = [
        c for c in verdict.attempts[0].claims if c.status == "unmatched"
    ]
    assert unmatched[0].surface == "14,600"
    assert "not among" in unmatched[0].reason
    assert "unsupported" in verdict.reason


def test_pack_can_harden_unmatched_final_to_refuse():
    judge_no = LLMResponse(content="NO", model="s")
    verifier, _ = make_verifier(
        [judge_no], settings=VerifierSettings(unmatched_final="refuse")
    )
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="prose", text="There were 14,600 invoices."),
        evidence=EVIDENCE,
        attempt=2,  # beyond max_regenerate_retries=1
    )
    assert result.disposition == "refused"


def test_entity_hallucination_is_unmatched_without_a_judge():
    verifier, llm = make_verifier([])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(
            kind="prose",
            text="The `rule_totals_check` function drives invoice_count.",
        ),
        evidence=EVIDENCE,
        attempt=2,
    )
    assert llm.calls == []  # entities never judged
    statuses = {c.surface: c.status for c in result.attempt_record.claims}
    assert statuses["`rule_totals_check`"] == "unmatched"
    # invoice_count IS in evidence (a result column name).
    assert statuses["invoice_count"] == "matched_exact"


def test_injected_claims_are_verified_by_construction():
    # Addendum N3: a claim contained in an injected span was written by
    # code from a resolved evidence path — recorded matched_injected
    # with that path as its basis, never prosecuted.
    verifier, llm = make_verifier([])
    text = "Of 146 invoices, many had findings."
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(
            kind="prose",
            text=text,
            injected_spans=[
                InjectedSpan(start=3, end=6, ref="e0.rows[0].n")  # the "146"
            ],
        ),
        evidence=EVIDENCE,
        attempt=1,
    )
    (claim,) = result.attempt_record.claims
    assert claim.injected is True
    assert claim.status == "matched_injected"
    assert claim.evidence_ref == "e0.rows[0].n"
    assert result.disposition == "verified"
    assert llm.calls == []  # no judge spend on code-written values


def test_sixteen_injected_line_numbers_cost_no_judge_budget():
    # Addendum N3, the L2 regression: 16 code-injected line numbers —
    # values no check harvests — verified by construction instead of
    # exhausting the judge against a corpus that cannot contain them.
    verifier, llm = make_verifier([])
    text = "Call order:"
    spans = []
    for i in range(16):
        value = str(100 + i * 7)
        text += " line "
        spans.append(
            InjectedSpan(
                start=len(text),
                end=len(text) + len(value),
                ref=f"e0.edges[{i}].line",
            )
        )
        text += value + ","
    result = verifier.verify(
        question="what does it call, in order?",
        draft=DraftAnswer(kind="prose", text=text, injected_spans=spans),
        evidence=EVIDENCE,
        attempt=1,
    )
    assert result.disposition == "verified"
    assert result.judge_calls == 0 and llm.calls == []
    numeric = [c for c in result.attempt_record.claims if c.kind == "numeric"]
    assert len(numeric) == 16
    assert all(c.status == "matched_injected" for c in numeric)


def test_a_claim_extending_past_an_injected_span_verifies_normally():
    # Addendum N3: containment, not overlap — a claim with model-typed
    # characters outside the span is prosecuted like any other.
    verifier, _ = make_verifier([])
    text = "Totals reach 1460 this week."
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(
            kind="prose",
            text=text,
            # Code injected "146"; the model typed the trailing 0,
            # making the extracted claim 1460 — partially injected.
            injected_spans=[InjectedSpan(start=13, end=16, ref="e0.rows[0].n")],
        ),
        evidence=EVIDENCE,
        attempt=2,
    )
    (claim,) = [c for c in result.attempt_record.claims if c.kind == "numeric"]
    assert claim.surface == "1460"
    assert claim.injected is True  # overlap still annotates honestly
    assert claim.status == "unmatched"  # but construction proves nothing


def test_table_passthrough_with_clean_caption_verifies():
    verifier, _ = make_verifier([])
    result = verifier.verify(
        question="q",
        draft=DraftAnswer(kind="table_passthrough", text=LAST_WEEK_SQL),
        evidence=EVIDENCE,
        attempt=1,
    )
    assert result.disposition == "verified"
