"""Mechanical claim matching (§9.2 step 2) — pure code over the merged
evidence pools.

The derivation menu is deliberately closed: rounding to the claim's
displayed resolution, ratios over constrained pairs, differences of
counts. A matcher that can derive anything verifies nothing; whatever
the menu cannot settle falls to the judge (numeric residue) or goes
unmatched (entities, quotes, dates — never judged).
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.config.models import VerifierSettings
from engine.verifier.models import (
    Claim,
    CorpusText,
    EntityClaim,
    EvidenceContribution,
    EvidenceValue,
    LineRef,
    NumericClaim,
    QuoteClaim,
)


class EvidencePools(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numbers: list[EvidenceValue] = []
    line_refs: list[LineRef] = []
    vocabulary: set[str] = set()
    strings: set[str] = set()
    quote_corpus: list[CorpusText] = []


def merge_contributions(
    contributions: list[EvidenceContribution],
) -> EvidencePools:
    pools = EvidencePools()
    for contribution in contributions:
        pools.numbers.extend(contribution.numbers)
        pools.line_refs.extend(contribution.line_refs)
        pools.vocabulary |= contribution.vocabulary
        pools.strings |= contribution.strings
        pools.quote_corpus.extend(contribution.quote_corpus)
    return pools


class MatchOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "fuzzy" = mechanically unsettled numeric residue for the judge.
    status: Literal["matched_exact", "matched_derived", "fuzzy", "unmatched"]
    method: str = ""
    matched_value: str | None = None
    evidence_ref: str | None = None
    reason: str = ""


def _comparable(claim: NumericClaim, value: EvidenceValue) -> list[float]:
    """The spaces a pool value may be compared in. The percent bridge
    (0.15 supports "15%") applies to stats and literals only — a
    random result cell 0.342 must not license "34.2%"."""
    spaces = [value.value]
    if (
        claim.is_percent
        and 0.0 <= value.value <= 1.0
        and value.salience in ("stat", "literal")
    ):
        spaces.append(value.value * 100.0)
    return spaces


def _rel_close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance * max(1.0, abs(b))


def _match_numeric(
    claim: NumericClaim, pools: EvidencePools, settings: VerifierSettings
) -> MatchOutcome:
    if claim.date is not None:
        for candidate in pools.strings:
            if candidate == claim.date or candidate[:10] == claim.date:
                return MatchOutcome(
                    status="matched_exact",
                    method="date",
                    matched_value=candidate,
                )
        return MatchOutcome(
            status="unmatched", reason="date not present in evidence"
        )

    assert claim.value is not None
    tolerance = settings.numeric_rel_tolerance

    for value in pools.numbers:
        for comparable in _comparable(claim, value):
            if _rel_close(claim.value, comparable, tolerance):
                return MatchOutcome(
                    status="matched_exact",
                    method="exact",
                    matched_value=repr(comparable),
                    evidence_ref=value.ref,
                )

    if claim.resolution is not None:
        for value in pools.numbers:
            for comparable in _comparable(claim, value):
                if abs(claim.value - comparable) <= claim.resolution:
                    return MatchOutcome(
                        status="matched_derived",
                        method="rounding",
                        matched_value=repr(comparable),
                        evidence_ref=value.ref,
                    )

    derived = _derive(claim, pools, settings)
    if derived is not None:
        return derived

    # Comparator claims are judge territory: code-side "at least"
    # logic is the derive-anything trap.
    return MatchOutcome(
        status="fuzzy", reason="no mechanical match or derivation"
    )


def _derive(
    claim: NumericClaim, pools: EvidencePools, settings: VerifierSettings
) -> MatchOutcome | None:
    numbers = pools.numbers
    budget = settings.max_derivation_pairs
    resolution = claim.resolution if claim.resolution is not None else 0.5

    def ratio_pairs():
        for a in numbers:
            for b in numbers:
                # b == 1 is a degenerate denominator: a/1 * 100 would
                # quietly re-open the percent bridge for any cell.
                if a is b or b.value in (0.0, 1.0):
                    continue
                same_row = (
                    a.group is not None and a.group == b.group
                )
                sums = "sum(" in a.ref and "sum(" in b.ref
                part_of_count = (
                    a.salience in ("cell", "count") and b.salience == "count"
                )
                if same_row or sums or part_of_count:
                    yield a, b

    if claim.is_percent or (claim.value is not None and 0 < claim.value <= 1):
        target = claim.value
        seen = 0
        for a, b in ratio_pairs():
            seen += 1
            if seen > budget:
                return MatchOutcome(
                    status="fuzzy",
                    reason=f"derivation pair cap ({budget}) reached",
                )
            ratio = a.value / b.value
            candidates = [ratio * 100.0] if claim.is_percent else [ratio]
            for candidate in candidates:
                if abs(target - candidate) <= resolution:
                    return MatchOutcome(
                        status="matched_derived",
                        method="ratio",
                        matched_value=repr(candidate),
                        evidence_ref=f"{a.ref} / {b.ref}",
                    )

    if not claim.is_percent and claim.value == int(claim.value):
        counts = [
            v
            for v in numbers
            if v.salience == "count" and v.value == int(v.value)
        ]
        seen = 0
        for a in counts:
            for b in counts:
                if a is b:
                    continue
                seen += 1
                if seen > budget:
                    return MatchOutcome(
                        status="fuzzy",
                        reason=f"derivation pair cap ({budget}) reached",
                    )
                if abs((b.value - a.value) - claim.value) <= 0.5:
                    return MatchOutcome(
                        status="matched_derived",
                        method="difference",
                        matched_value=repr(b.value - a.value),
                        evidence_ref=f"{b.ref} - {a.ref}",
                    )
    return None


def _match_entity(claim: EntityClaim, pools: EvidencePools) -> MatchOutcome:
    if claim.subkind == "location":
        for ref in pools.line_refs:
            if claim.file_path is not None and not ref.file_path.endswith(
                claim.file_path
            ):
                continue
            if (
                claim.line_start is not None
                and claim.line_start >= ref.start
                and (claim.line_end or claim.line_start) <= ref.end
            ):
                return MatchOutcome(
                    status="matched_exact",
                    method="location",
                    matched_value=f"{ref.file_path}:{ref.start}-{ref.end}",
                    evidence_ref=ref.ref,
                )
        return MatchOutcome(
            status="unmatched",
            reason="location not within any retrieved line range",
        )

    if claim.entity in pools.vocabulary:
        return MatchOutcome(status="matched_exact", method="vocabulary")
    return MatchOutcome(
        status="unmatched",
        reason="name not present in this turn's evidence",
    )


_WHITESPACE = re.compile(r"\s+")
_ELISION = {"...", "…"}


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _match_quote(claim: QuoteClaim, pools: EvidencePools) -> MatchOutcome:
    """Whitespace-normalized substring match. Case and punctuation are
    meaning in code, so they are NOT normalized — and quotes never go
    to the judge: a judge blessing a near-quote is the exact failure
    class this system exists to kill."""
    lines = [
        _normalize(line)
        for line in claim.text.splitlines()
        if _normalize(line) and _normalize(line) not in _ELISION
    ]
    if not lines:
        return MatchOutcome(status="matched_exact", method="quote-empty")

    whole = _normalize(claim.text)
    for corpus in pools.quote_corpus:
        haystack = _normalize(corpus.text)
        if whole in haystack:
            return MatchOutcome(
                status="matched_exact", method="quote", evidence_ref=corpus.ref
            )
        if len(lines) > 1 and all(line in haystack for line in lines):
            return MatchOutcome(
                status="matched_exact",
                method="quote-lines",
                evidence_ref=corpus.ref,
            )
    return MatchOutcome(
        status="unmatched",
        reason="quoted text not found in retrieved content",
    )


def match_claim(
    claim: Claim, pools: EvidencePools, settings: VerifierSettings
) -> MatchOutcome:
    if isinstance(claim, NumericClaim):
        return _match_numeric(claim, pools, settings)
    if isinstance(claim, EntityClaim):
        return _match_entity(claim, pools)
    return _match_quote(claim, pools)


def nearest_values(
    claim: NumericClaim, pools: EvidencePools, limit: int
) -> list[tuple[float, EvidenceValue]]:
    """Judge candidates: pool values ranked by proximity, percent
    claims ranked in raw and x100 space."""
    if claim.value is None:
        return []
    ranked: list[tuple[float, float, EvidenceValue]] = []
    for value in pools.numbers:
        spaces = [value.value]
        if claim.is_percent and 0.0 <= value.value <= 1.0:
            spaces.append(value.value * 100.0)
        best = min(spaces, key=lambda s: abs(claim.value - s))
        ranked.append((abs(claim.value - best), best, value))
    ranked.sort(key=lambda item: item[0])
    return [(shown, value) for _, shown, value in ranked[:limit]]
