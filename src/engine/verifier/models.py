"""Verifier contract models: what the harness hands in, what comes
back per attempt, and the claim-level verdict that lands verbatim in
turn_log.verifier_verdict.

The harness depends on exactly these shapes (its seam); the verifier's
internal claim/evidence-pool models live beside the code that builds
them (claims.py, matching.py).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.config.models import ToolName


class DraftAnswer(BaseModel):
    """The final answer text as it would ship — placeholders already
    resolved. injected_spans are the char ranges code wrote (injected
    figures verify like any claim; they simply cannot mismatch)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prose", "table_passthrough"]
    text: str
    injected_spans: list[tuple[int, int]] = []


class ClaimRecord(BaseModel):
    """One extracted claim and how it fared. start/end are char offsets
    into the draft text — the Phase 5 inspector highlights from them."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["numeric", "entity", "quote"]
    surface: str
    start: int
    end: int
    status: Literal[
        "matched_exact", "matched_derived", "matched_judge", "unmatched"
    ]
    method: str = ""  # "exact", "rounding", "ratio", "judge", ...
    matched_value: str | None = None
    evidence_ref: str | None = None
    reason: str = ""
    injected: bool = False


class AttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int
    claims: list[ClaimRecord]
    unmatched_count: int


class PlausibilityRecord(BaseModel):
    """An evidence-side sanity finding (§9.3). fail means the evidence
    contradicts what is independently known; warn is a soft band."""

    model_config = ConfigDict(extra="forbid")

    check: str  # e.g. "run_sql.count_vs_stats"
    tool: ToolName
    severity: Literal["fail", "warn"]
    detail: str


class FeedbackItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: str
    sentence: str
    kind: Literal["numeric", "entity", "quote"]
    nearest_evidence: list[str] = []


class RegenerationFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FeedbackItem]


class VerifierVerdict(BaseModel):
    """The final ladder outcome for a turn, with claim-level detail —
    serialized whole into turn_log.verifier_verdict."""

    model_config = ConfigDict(extra="forbid")

    disposition: Literal["verified", "unverified", "refused"]
    mode: Literal["prose", "table_passthrough"]
    attempts: list[AttemptRecord]
    plausibility: list[PlausibilityRecord]
    judge_calls: int
    reason: str = ""


class VerifierResult(BaseModel):
    """One verification attempt's outcome, as the verify node consumes
    it. disposition "retry" never reaches a final verdict — it drives
    the redraft loop; the attempts it spawned are all recorded."""

    model_config = ConfigDict(extra="forbid")

    disposition: Literal["verified", "retry", "unverified", "refused"]
    attempt_record: AttemptRecord
    plausibility: list[PlausibilityRecord]
    feedback: RegenerationFeedback | None = None
    judge_calls: int = 0
