"""The Verifier: one verify() call per drafting attempt (§9.2).

Pipeline per call: harvest evidence pools via the registered checks →
extract claims deterministically → match mechanically → judge the
numeric residue (budgeted) → run plausibility → decide the ladder
step. The retry LOOP lives in the graph; the retry DECISION and the
feedback live here.

Claims contained in an injected span were written by code from a
resolved evidence path — faithful by construction (§9.4) — and are
recorded matched_injected with that path as their basis; the matcher
and judge prosecute only model-typed spans. A claim extending beyond
an injected span contains model-typed characters and verifies
normally. Table pass-through answers run the same pipeline: caption
prose usually yields zero claims (honest — the numbers never passed
through a model) and plausibility still runs, so a doctored result
set is refused no matter how it would have shipped.
"""

from collections.abc import Callable
from typing import Literal

from engine.config.models import VerifierSettings
from engine.ports.llm import LLMPort
from engine.substrates.models import StatsRow
from engine.tools.envelope import ToolInvocation
from engine.verifier.checks.base import CheckRegistry, PlausibilityContext
from engine.verifier.claims import containing_sentence, extract_claims
from engine.verifier.judge import JudgeRunner
from engine.verifier.matching import (
    EvidencePools,
    match_claim,
    merge_contributions,
)
from engine.verifier.models import (
    AttemptRecord,
    Claim,
    ClaimRecord,
    DraftAnswer,
    InjectedSpan,
    NumericClaim,
    PlausibilityRecord,
    QuoteClaim,
    VerifierResult,
)
from engine.verifier.verdict import build_feedback, decide


def _overlaps(claim: Claim, spans: list[InjectedSpan]) -> bool:
    return any(
        claim.start < span.end and claim.end > span.start for span in spans
    )


def _containing_span(
    claim: Claim, spans: list[InjectedSpan]
) -> InjectedSpan | None:
    """The injected span that wholly contains the claim, or None.
    Containment, not overlap: a claim that extends past an injected
    span has model-typed characters and must verify normally."""
    for span in spans:
        if span.start <= claim.start and claim.end <= span.end:
            return span
    return None


class Verifier:
    def __init__(
        self,
        checks: CheckRegistry,
        llm: LLMPort,
        settings: VerifierSettings,
        stats_provider: Callable[[], list[StatsRow]],
    ) -> None:
        self._checks = checks
        self._llm = llm
        self._settings = settings
        self._stats_provider = stats_provider
        self._stats: list[StatsRow] | None = None

    def _load_stats(self) -> list[StatsRow]:
        if self._stats is None:
            try:
                self._stats = self._stats_provider()
            except Exception:
                # No stats substrate -> no plausibility reference; the
                # checks defined against it simply have nothing to say
                # (§9.1: plausibility "wherever a check is defined").
                self._stats = []
        return self._stats

    def _pools(self, evidence: list[ToolInvocation]) -> EvidencePools:
        contributions = []
        for index, invocation in enumerate(evidence):
            if invocation.status != "ok" or invocation.output is None:
                continue  # failed calls support no claims
            check = self._checks.for_tool(invocation.tool)
            if check is None:
                continue
            contributions.append(check.harvest(invocation, f"e{index}"))
        return merge_contributions(contributions)

    def _plausibility(
        self, evidence: list[ToolInvocation]
    ) -> list[PlausibilityRecord]:
        ctx = PlausibilityContext(
            stats=self._load_stats(), settings=self._settings.plausibility
        )
        records: list[PlausibilityRecord] = []
        for invocation in evidence:
            if invocation.status != "ok" or invocation.output is None:
                continue
            check = self._checks.for_tool(invocation.tool)
            if check is None:
                continue
            records.extend(
                PlausibilityRecord(
                    check=finding.check,
                    tool=invocation.tool,
                    severity=finding.severity,
                    detail=finding.detail,
                )
                for finding in check.plausibility(invocation, ctx)
            )
        return records

    @staticmethod
    def _claims_of(draft: DraftAnswer, pools: EvidencePools) -> list[Claim]:
        """A table pass-through caption that is itself a verbatim quote
        of evidence (the harness passes the SQL through) carries no
        independent claims — it IS evidence. Anything else, including
        a caption someone edited, extracts normally."""
        if draft.kind == "table_passthrough":
            text = draft.text.strip()
            if not text:
                return []
            probe = QuoteClaim(surface=text, start=0, end=len(text), text=text)
            outcome = match_claim(probe, pools, VerifierSettings())
            if outcome.status == "matched_exact":
                return []
        return extract_claims(draft.text)

    def verify(
        self,
        *,
        question: str,
        draft: DraftAnswer,
        evidence: list[ToolInvocation],
        attempt: int,
    ) -> VerifierResult:
        pools = self._pools(evidence)
        claims = self._claims_of(draft, pools)
        judge = JudgeRunner(self._llm, self._settings.judge)

        records: list[ClaimRecord] = []
        unmatched_pairs: list[tuple[ClaimRecord, Claim]] = []
        for claim in claims:
            span = _containing_span(claim, draft.injected_spans)
            if span is not None:
                # Code wrote this value from a resolved evidence path;
                # it cannot mismatch. Record the basis, spend no judge.
                records.append(
                    ClaimRecord(
                        kind=claim.kind,
                        surface=claim.surface,
                        start=claim.start,
                        end=claim.end,
                        status="matched_injected",
                        method="injected",
                        evidence_ref=span.ref,
                        reason="injected by placeholder resolution",
                        injected=True,
                    )
                )
                continue
            outcome = match_claim(claim, pools, self._settings)
            status: Literal[
                "matched_exact", "matched_derived", "matched_judge", "unmatched"
            ]
            method, reason = outcome.method, outcome.reason
            if outcome.status == "fuzzy":
                assert isinstance(claim, NumericClaim)
                sentence = containing_sentence(
                    draft.text, claim.start, claim.end
                )
                supported, judge_reason = judge.judge(claim, sentence, pools)
                if supported:
                    status, method = "matched_judge", "judge"
                    reason = judge_reason
                else:
                    status, reason = "unmatched", judge_reason
            else:
                status = outcome.status

            record = ClaimRecord(
                kind=claim.kind,
                surface=claim.surface,
                start=claim.start,
                end=claim.end,
                status=status,
                method=method,
                matched_value=outcome.matched_value,
                evidence_ref=outcome.evidence_ref,
                reason=reason,
                injected=_overlaps(claim, draft.injected_spans),
            )
            records.append(record)
            if status == "unmatched":
                unmatched_pairs.append((record, claim))

        attempt_record = AttemptRecord(
            attempt=attempt,
            claims=records,
            unmatched_count=len(unmatched_pairs),
        )
        plausibility = self._plausibility(evidence)
        disposition = decide(
            len(unmatched_pairs), plausibility, attempt, self._settings
        )
        feedback = None
        if disposition == "retry":
            feedback = build_feedback(draft.text, unmatched_pairs, pools)
        return VerifierResult(
            disposition=disposition,
            attempt_record=attempt_record,
            plausibility=plausibility,
            feedback=feedback,
            judge_calls=judge.calls_made,
        )
