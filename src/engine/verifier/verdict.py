"""Verdict ladder mechanics (§9.2 step 4, per the approved amendment):

- all claims accounted for, no plausibility findings -> verified
- unmatched claims, retries left -> retry with mismatch feedback
- unmatched claims, retries exhausted -> unverified (pack may harden
  to refuse), the label explicit, the text untouched
- any plausibility FAIL -> refused immediately, no retry: the
  evidence is wrong, and redrafting the same evidence can only yield
  a fluent wrong answer — the critical failure class
- plausibility WARN caps a clean answer at unverified

Pure code: aggregation, feedback rendering, and the final verdict.
"""

from typing import Literal

from engine.config.models import VerifierSettings
from engine.verifier.claims import containing_sentence
from engine.verifier.matching import EvidencePools, nearest_values
from engine.verifier.models import (
    AttemptRecord,
    ClaimRecord,
    FeedbackItem,
    NumericClaim,
    PlausibilityRecord,
    RegenerationFeedback,
    VerifierVerdict,
)


def build_feedback(
    draft_text: str,
    unmatched: list[tuple[ClaimRecord, object]],
    pools: EvidencePools,
) -> RegenerationFeedback:
    items = []
    for record, claim in unmatched:
        nearest: list[str] = []
        if isinstance(claim, NumericClaim) and claim.value is not None:
            nearest = [
                f"{shown!r} ({value.salience}, {value.ref})"
                for shown, value in nearest_values(claim, pools, 3)
            ]
        items.append(
            FeedbackItem(
                surface=record.surface,
                sentence=containing_sentence(
                    draft_text, record.start, record.end
                ),
                kind=record.kind,
                nearest_evidence=nearest,
            )
        )
    return RegenerationFeedback(items=items)


def render_feedback(feedback: RegenerationFeedback) -> list[str]:
    """Feedback items as the drafter-facing strings."""
    lines = []
    for item in feedback.items:
        line = (
            f"{item.surface!r} in {item.sentence!r} is not supported by "
            "the tool evidence."
        )
        if item.nearest_evidence:
            line += " Closest actual values: " + "; ".join(
                item.nearest_evidence
            )
        lines.append(line)
    return lines


def decide(
    unmatched_count: int,
    plausibility: list[PlausibilityRecord],
    attempt: int,
    settings: VerifierSettings,
) -> Literal["verified", "retry", "unverified", "refused"]:
    if any(record.severity == "fail" for record in plausibility):
        return "refused"
    if unmatched_count == 0:
        if any(record.severity == "warn" for record in plausibility):
            return "unverified"
        return "verified"
    if attempt <= settings.max_regenerate_retries:
        return "retry"
    return "refused" if settings.unmatched_final == "refuse" else "unverified"


def finalize(
    attempts: list[AttemptRecord],
    plausibility: list[PlausibilityRecord],
    mode: Literal["prose", "table_passthrough"],
    judge_calls: int,
    disposition: Literal["verified", "unverified", "refused"],
) -> VerifierVerdict:
    reason = ""
    fails = [r for r in plausibility if r.severity == "fail"]
    warns = [r for r in plausibility if r.severity == "warn"]
    if fails:
        reason = "implausible evidence: " + "; ".join(r.detail for r in fails)
    elif disposition == "unverified" and attempts and attempts[-1].unmatched_count:
        reason = (
            f"{attempts[-1].unmatched_count} claim(s) unsupported after "
            f"{len(attempts)} attempt(s)"
        )
    elif disposition == "unverified" and warns:
        reason = "plausibility warning: " + "; ".join(r.detail for r in warns)
    elif disposition == "refused" and attempts:
        reason = (
            f"{attempts[-1].unmatched_count} claim(s) unsupported; pack "
            "refuses unverified answers"
        )
    return VerifierVerdict(
        disposition=disposition,
        mode=mode,
        attempts=attempts,
        plausibility=plausibility,
        judge_calls=judge_calls,
        reason=reason,
    )
