"""Verifier contract models: what the harness hands in, what comes
back per attempt, and the claim-level verdict that lands verbatim in
turn_log.verifier_verdict.

The harness depends on exactly these shapes (its seam); the verifier's
internal claim/evidence-pool models live beside the code that builds
them (claims.py, matching.py).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.config.models import ToolName


class InjectedSpan(BaseModel):
    """A char range of the resolved draft that code wrote, and the
    evidence path it was resolved from. Injected values are faithful
    by construction (§9.4) — the ref is their verification basis."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    ref: str  # dotted evidence path, e.g. "e1.run_status.count"


class DraftAnswer(BaseModel):
    """The final answer text as it would ship — placeholders already
    resolved. Claims contained in injected_spans are verified by
    construction against their resolution refs; the matcher and judge
    prosecute only model-typed spans."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prose", "table_passthrough"]
    text: str
    injected_spans: list[InjectedSpan] = []


class NumericClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["numeric"] = "numeric"
    surface: str  # "1,442,986", "$1,200.50", "34.2%", "1.4 million"
    start: int
    end: int
    value: float | None = None  # None for date-form claims
    date: str | None = None  # ISO YYYY-MM-DD; MM-DD for yearless prose
    is_percent: bool = False
    is_currency: bool = False
    is_approximate: bool = False
    comparator: Literal["over", "under", "at_least", "at_most"] | None = None
    # Half the last displayed unit: "1.4 million" -> 50_000, "34.2%"
    # -> 0.05, "146" -> 0.5. Drives the mechanical rounding match.
    resolution: float | None = None
    # True for spelled cardinals ("twelve") — eligible for the quote-
    # corpus fallback when no harvested value matches.
    spelled: bool = False


class EntityClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["entity"] = "entity"
    surface: str
    start: int
    end: int
    entity: str
    subkind: Literal["identifier", "location"] = "identifier"
    # location claims only: rules_engine.py:116-149
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


class QuoteClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quote"] = "quote"
    surface: str
    start: int
    end: int
    text: str
    fenced: bool = False


Claim = Annotated[
    NumericClaim | EntityClaim | QuoteClaim, Field(discriminator="kind")
]


class EvidenceValue(BaseModel):
    """One quotable number from the evidence. salience separates what
    a claim may match: result cells, counts, statistics, and code/doc
    literals behave differently in percent bridging and derivations."""

    model_config = ConfigDict(extra="forbid")

    value: float
    ref: str  # dotted path, e.g. "e1.table.rows[0].n"
    salience: Literal["cell", "count", "stat", "literal"]
    # Same-row grouping for ratio derivations ("e1.rows[0]"); None for
    # ungrouped values.
    group: str | None = None


class LineRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str
    start: int
    end: int
    ref: str


class CorpusText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    ref: str


class EvidenceContribution(BaseModel):
    """What one invocation's registered check harvests into the merged
    pools. Vocabulary is identifier-shaped names a drafted entity may
    cite; strings are exact values (cells, dates, slugs)."""

    model_config = ConfigDict(extra="forbid")

    numbers: list[EvidenceValue] = []
    line_refs: list[LineRef] = []
    vocabulary: set[str] = set()
    strings: set[str] = set()
    quote_corpus: list[CorpusText] = []


class PlausibilityFinding(BaseModel):
    """A check's raw finding; verify() stamps the tool to make the
    provenance-facing PlausibilityRecord."""

    model_config = ConfigDict(extra="forbid")

    check: str
    severity: Literal["fail", "warn"]
    detail: str


class ClaimRecord(BaseModel):
    """One extracted claim and how it fared. start/end are char offsets
    into the draft text — the Phase 5 inspector highlights from them."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["numeric", "entity", "quote"]
    surface: str
    start: int
    end: int
    status: Literal[
        "matched_exact",
        "matched_derived",
        "matched_judge",
        "matched_injected",
        "unmatched",
    ]
    method: str = ""  # "exact", "rounding", "ratio", "judge", "injected", ...
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
    plausibility: list[PlausibilityRecord] = []
    feedback: RegenerationFeedback | None = None
    judge_calls: int = 0
