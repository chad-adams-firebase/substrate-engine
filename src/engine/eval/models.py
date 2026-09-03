"""Eval harness contract models: bank rows, assertions, run reports.

Every shape that crosses a module boundary here is pydantic with
extra="forbid" (CLAUDE.md): a typo in a hand-authored bank row must
fail at load time, not silently grade as vacuously true.

The assertion vocabulary is a closed discriminated union, like the
tool surface: the grader evaluates registered assertion kinds, never
ad-hoc predicates. Each kind declares whether it is a CONTENT
assertion — the class whose failure at exit 0 constitutes the
wrong-but-verified invariant breach (docs/phase4-gate-verdict.md §7.1)
— or a SHAPE assertion (format, route, retry), whose failure is a row
failure but never a breach.
"""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.config.models import ToolName
from engine.harness.events import StatusEvent
from engine.harness.outcomes import TurnOutcome
from engine.tools.envelope import DurationUnit
from engine.verifier.models import VerifierVerdict

# The open-backlog anomaly refs an expected-fail row may carry
# (docs/phase4-gate-verdict.md §6 condition of closure), plus WBV-S4,
# the one 4b wrong-but-verified row still annotated
# (docs/phase4b-baseline-findings.md §2) — ledger honesty while its
# residual is graded: breach detection pierces xfail by design, so
# the before-picture stays loud. Flipping a row to expected-pass is a
# deliberate bank edit: delete the xfail block when the fix lands.
# Retired after two clean runs apiece: WBV-C4/MT2/S7/U5 (fp3-rerun +
# fp3-confirm XPASS); clarify-open went earlier, after 522 live turns
# without a clarify (the ambiguity rows now test a named reading).
# N9/N10 retired on fp4-slice's 5/5 XPASSes (B4, MT3, P-L3Q; C1, C1b)
# — a single run, but code-backed by fix pass 4 (unlike the 6c0e848
# luck-flip), and N9's acceptance condition (all three sites 5/5
# simultaneously) was met in full.
# N11/N12 retired by re-attribution, not flip: fp4b-holdouts proved
# both mechanisms (P-N11 reached 5/5 drafting e1's error_count via
# placeholder; HN-ERRORS rep 3 verified at exit 0, the row's first
# delivered answer) while the rows stayed XFAIL on a different
# class — the poolless identifiers the same rows now track as N13.
# A third retirement shape beside the XPASS flip and the 6c0e848
# luck-flip reversal: the block keeps its row, the ref changes.
# N13 retired on n13-witnesses with no remaining rows: HN-ERRORS 5/5
# verified at exit 0 (its block deleted — the contains pattern, not
# the answers, was the defect), NP6 threshold-clearing, zero
# backticked failures run-wide. P-N11's block re-attributed to N5:
# N11 and N13 proven on its one reached rep; what it tracks now is
# the licensed retry's firing rate (reached 1/5).
# N5 retired on the full post-N13 bank (Phase 5 opening rider): P-N11
# XPASS 4/4 with reached 4/5 — the MUST-form retry sentence moved the
# firing rate, and the row's setup block (min_errored/min_ok) now
# states the scenario directly, so the annotation had nothing left to
# explain. Block deleted; no remaining N5 rows.
# P-N11 retired on the post-pin-pass bank (Phase 5 Block 2): reached
# 0/5, INCON — the play pass's definitional vocabulary names every
# component in the router prompt, so the errored-then-retry scenario
# the row probed no longer occurs live. The licensed retry stays
# unit-tested (test_harness_router.py); the row is deleted, its gold
# script kept for HN-ERRORS. A fourth retirement shape: the scenario
# starved by an unrelated fix, not the anomaly fixed.
# ASSOC (opened in the Phase 5 interlude play pass): the Verifier
# checks entity existence, not association. W4 zipped the 12 audit
# rules against their descriptions offset by 6 — every name real,
# every description real, every pairing wrong — and verified. W2
# attributed every invoice's reports and feedback to every auditor
# who touched the invoice; its COUNT(DISTINCT) aggregates are
# lint-exempt by design and not numerically fanned, so no generic
# check can see the mis-attribution (cross-entity semantics, the
# play pass's declared out-of-scope). Association/attribution
# verification is a queued design item; these rows measure the gap
# until it lands.
# WBV-S4 retired on the coverage pass (Phase 5 interlude, 2026-09-02):
# XPASS 5/5 on three consecutive runs under one model pin (98b3232).
# The standard is revised with its reason: a pinned, reproducible model
# is not luck — three stable runs on one pin attribute to the pin, and
# an xfail that predicts nothing masks the regression it would have
# caught. A block whose XPASS is stable across three runs on one pin
# comes off with that sentence in the row's note. ASSOC is unaffected:
# W4's pairing is a checked-nowhere property, not a stable habit.
# Duration pass (2026-09-02): a block whose property is checked
# nowhere carries keep_until naming the milestone that retires it, so
# an XPASS reads as a deliberate keep instead of drawing the deletion
# prompt every run (W4 XPASS 4/5 post-coverage, again by habit).
XfailRef = Literal[
    "O1",
    "ASSOC",
]


class Xfail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: XfailRef
    # The root-cause annotation the gate verdict requires — why this
    # row is expected to fail, in terms of the anomaly's mechanism.
    note: str
    # The milestone whose landing retires this block. While set, an
    # XPASS is a deliberate keep — the grader says so instead of asking
    # for deletion — because the property the block names is checked
    # nowhere yet (ASSOC: W4's pairing is right by the pinned model's
    # habit, not by construction). The block still comes off in a
    # reviewed bank edit, never on a pass rate.
    keep_until: str | None = None


# --- Assertions --------------------------------------------------------


class _AssertionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # True: failing this at exit 0 is a wrong-but-verified breach.
    content: ClassVar[bool] = False
    # Per-assertion expected-fail (e.g. O1's dump guard on a row whose
    # other assertions must still gate). Row-level xfail covers the
    # common case; this covers one assertion on an otherwise-green row.
    xfail_ref: XfailRef | None = None
    # False: an omission-tolerant content assertion — it still gates the
    # rep's pass/fail, but its failure at exit 0 is not a breach. For
    # values a correct answer has no obligation to state (S6's amount
    # when the question asks which supplier). Applied deliberately, per
    # assertion, where a findings document justifies it; never a row
    # default. Orthogonal to breach-by-kind (grade._alarm_worthy): a
    # contains failure never breaches regardless of this flag. A
    # breach:false gold-numeric miss at exit 0 is still a WBV-class
    # event: the grader counts it as a documented miss so "INVARIANT
    # ok" reads precisely as "zero UNDOCUMENTED wrong-but-verified".
    breach: bool = True
    # Restrict the assertion to reps whose exit_equiv is listed — the
    # per-exit half of a row that accepts several exits (AMB2: the
    # numeric guard gates exit-0 reps, the clarify-shape check gates
    # exit-4 reps; a clarify body has no gold token to carry). None
    # applies at every exit, as before.
    at_exit: list[int] | None = None


class NonEmptyAssertion(_AssertionBase):
    """The N7 shrug guard: a markdown answer has non-blank prose, a
    table answer has at least one row."""

    kind: Literal["nonempty"] = "nonempty"


class NumericFromGoldAssertion(_AssertionBase):
    """The gold script's value appears in the answer body: any table
    cell, or any numeric extracted from the prose (digit groups with
    $,%, commas stripped; word-numbers one..twenty mapped). A gold
    ratio in [0,1] also matches its percent form. A table's caption
    (the SQL) is not in the pool — its literals are not stated
    values.

    `field` may list several gold fields, any of which satisfies the
    assertion — for ambiguity rows whose gold computes every
    documented reading (AMB2: READY or not-CLOSED). A number matching
    no listed reading is wrong content; the duality is declared per
    row, never inferred.

    `unit` makes the comparison unit-aware (duration pass, W3 rep 5:
    a correct "60 minutes" graded contradicted against gold 1.0
    hours). With a unit declared the gold converts to seconds and the
    answer's stated DURATIONS — "1 hour", "60 minutes", "1.1 days",
    "twelve seconds", H:MM:SS — compare in seconds, within half a
    displayed decimal of the phrase's own unit. A bare number is not a
    stated duration under a unit: "1.0 days" must not pass a
    1.0-hours gold on its digits."""

    content: ClassVar[bool] = True

    kind: Literal["numeric_from_gold"] = "numeric_from_gold"
    field: str | list[str]  # key(s) into the gold script's returned dict
    unit: DurationUnit | None = None  # the gold's unit, when it is a duration

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "NumericFromGoldAssertion":
        if isinstance(self.field, list) and not self.field:
            raise ValueError("numeric_from_gold needs at least one gold field")
        return self

    def fields(self) -> list[str]:
        return [self.field] if isinstance(self.field, str) else self.field


class NameFromGoldAssertion(_AssertionBase):
    """Who-questions return people, not ids: the gold name (from an
    executed roster query) appears word-bounded and case-folded, and
    the answer does not present a bare id as the person.

    `field` may list several gold fields, any of which satisfies the
    assertion — for entities the grounding mandate (f709d9c) renders
    under a joined display name, where the code and the name are both
    correct (C5/MT3: supplier RVX01 is Ravenswood Extrusion). The
    duality is declared per row, never inferred: for most rows the
    single field IS the only right label."""

    content: ClassVar[bool] = True

    kind: Literal["name_from_gold"] = "name_from_gold"
    field: str | list[str] = "name"
    forbid_bare_ids: bool = True

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "NameFromGoldAssertion":
        if isinstance(self.field, list) and not self.field:
            raise ValueError("name_from_gold needs at least one gold field")
        return self

    def fields(self) -> list[str]:
        return [self.field] if isinstance(self.field, str) else self.field


class ContainsAssertion(_AssertionBase):
    content: ClassVar[bool] = True

    kind: Literal["contains"] = "contains"
    pattern: str
    regex: bool = False
    case_sensitive: bool = False


class NotContainsAssertion(_AssertionBase):
    """Either a literal/regex pattern, or every string in a gold list
    field (e.g. the executed roster feeding B6's 'no named person')."""

    content: ClassVar[bool] = True

    kind: Literal["not_contains"] = "not_contains"
    pattern: str | None = None
    regex: bool = False
    case_sensitive: bool = False
    from_gold_field: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "NotContainsAssertion":
        if (self.pattern is None) == (self.from_gold_field is None):
            raise ValueError(
                "not_contains needs exactly one of pattern / from_gold_field"
            )
        return self


class PatternCountAssertion(_AssertionBase):
    """The pattern occurs exactly N times in the answer — N from a gold
    field or a literal. The flagship table's twelve `—` cells (six
    suppliers with no invoices, two columns) are a count, and a count
    is gold's business like any other number (Polish Pass, W-E)."""

    content: ClassVar[bool] = True

    kind: Literal["pattern_count"] = "pattern_count"
    pattern: str
    regex: bool = False
    case_sensitive: bool = False
    from_gold_field: str | None = None
    equals: int | None = None

    @model_validator(mode="after")
    def _exactly_one_count(self) -> "PatternCountAssertion":
        if (self.equals is None) == (self.from_gold_field is None):
            raise ValueError(
                "pattern_count needs exactly one of equals / from_gold_field"
            )
        return self


class CurrencyFormatAssertion(_AssertionBase):
    """Money renders as currency: every $-amount is comma-grouped with
    two decimals, and no float tail (8308.92139…) appears anywhere."""

    kind: Literal["currency_format"] = "currency_format"


class WindowDataAnchoredAssertion(_AssertionBase):
    """The A1/C4 trap guard: the executed SQL (or windowed tool
    arguments) in evidence contains the gold window's date literals
    and none of the forbidden wall-clock anchors. Read the window, not
    the count — a verified 0 through a real-today window is exactly
    what this catches.

    Two jobs, two weights: a forbidden anchor present is the breach;
    a gold literal absent is a convention mismatch that fails the rep
    only (the fp3 re-run's A1: the right count over a calendar week
    is not a wrong-but-verified answer)."""

    content: ClassVar[bool] = True

    kind: Literal["window_data_anchored"] = "window_data_anchored"
    field: str  # gold dict key holding the expected date literal(s)
    forbid: list[str] = ["CURRENT_DATE", "now(", "today"]


class RouteAssertion(_AssertionBase):
    """Graded against tools_used — one entry per invocation in call
    order, duplicates and failed invocations included."""

    kind: Literal["route"] = "route"
    # must_include requires every listed tool; must_include_any_of
    # requires at least one — the outcome-over-mechanism arm (pin
    # pass, PLAY-R1: either app_primer or lookup_data_dictionary
    # answers a definitional question; PLAY-R3 keeps the pure
    # must_include as the mechanism probe).
    mode: Literal[
        "first",
        "must_include",
        "must_include_any_of",
        "must_not_include",
        "exact_set",
    ]
    tools: list[ToolName]


class RetryCountAssertion(_AssertionBase):
    """The N5 license: exactly `errors` errored invocations of `tool`,
    each followed by a same-tool retry (counted from the evidence
    bundle's invocation order). A list names the counts accepted —
    REC-SQL's `[0, 1]`: a rep that rephrases before the bounce
    answered correctly, and pre-emptive recovery is recovery (guard
    pass)."""

    kind: Literal["retry_count"] = "retry_count"
    tool: ToolName
    errors: int | list[int] = 1
    error_contains: str = ""

    @model_validator(mode="after")
    def _counts_are_usable(self) -> "RetryCountAssertion":
        accepted = self.errors if isinstance(self.errors, list) else [self.errors]
        if not accepted or any(count < 0 for count in accepted):
            raise ValueError("retry_count errors must name one or more counts >= 0")
        return self


class EnvelopeAssertion(_AssertionBase):
    kind: Literal["envelope"] = "envelope"
    body: Literal["markdown", "table"]


class NoTextBlockDumpAssertion(_AssertionBase):
    """The O1 guard: no long answer substring is a verbatim paste of
    evidence payload text (whole descriptions/code blocks injected
    inline)."""

    kind: Literal["no_text_block_dump"] = "no_text_block_dump"
    min_length: int = 200


class VerdictCheckAssertion(_AssertionBase):
    """Probe rows assert verifier internals (e.g. L2's injected line
    numbers arrive matched_injected without exhausting the judge)."""

    kind: Literal["verdict_check"] = "verdict_check"
    max_judge_calls: int | None = None
    disposition: Literal["verified", "unverified", "refused"] | None = None
    min_injected_claims: int | None = None


Assertion = Annotated[
    NonEmptyAssertion
    | NumericFromGoldAssertion
    | NameFromGoldAssertion
    | ContainsAssertion
    | NotContainsAssertion
    | PatternCountAssertion
    | CurrencyFormatAssertion
    | WindowDataAnchoredAssertion
    | RouteAssertion
    | RetryCountAssertion
    | EnvelopeAssertion
    | NoTextBlockDumpAssertion
    | VerdictCheckAssertion,
    Field(discriminator="kind"),
]

GOLD_ASSERTION_KINDS = frozenset(
    {"numeric_from_gold", "name_from_gold", "window_data_anchored"}
)


# --- Bank rows ----------------------------------------------------------


class SetupSpec(BaseModel):
    """Scenario preconditions, evaluated per rep from the turn's
    recorded invocations before any outcome assertion. Probe rows
    exist to test a scenario, and twice a row silently failed to
    reach its scenario yet graded as if it had; a rep failing setup
    is scenario-not-reached — excluded from the pass-rate
    denominator, neither a pass nor a fail."""

    model_config = ConfigDict(extra="forbid")

    # Restrict the counts below to invocations of this tool.
    tool: str | None = None
    min_invocations: int | None = None
    min_errored: int | None = None
    min_ok: int | None = None
    # The rep reached its scenario only if the turn's exit equivalent
    # is one of these — W4's shape (coverage pass): a row that measures
    # a drafted answer's pairing is not reached by a refusal, which is
    # scenario-not-reached, not an expected failure. Mirrors
    # Expectation.exit's vocabulary (0 verified · 2 unverified · ...).
    exit: list[int] | None = None


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Exit-code equivalents that count as the expected shape
    # (0 verified · 2 unverified · 3 refuse · 4 clarify · 5 escalate).
    exit: list[int]
    setup: SetupSpec | None = None
    assertions: list[Assertion] = []


class BankTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    expect: Expectation


class BankRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provenance: Literal["scripted", "user-sourced", "n-probe"]
    category: Literal[
        "data",
        "code",
        "docs",
        "execution",
        "fail-closed",
        "sentinel",
        "routing",
        "recovery",
        "multiturn",
        "ambiguity",
        "honest-negative",
        "meta",
    ]
    # Exactly one of question/expect (single-turn) or turns.
    question: str | None = None
    expect: Expectation | None = None
    turns: list[BankTurn] = []
    # Path to the executable gold script, relative to the bank root.
    gold: str | None = None
    # Committed tripwire only — grade compares answers against the
    # EXECUTED gold value, never this transcription (the grader's-
    # correction law, docs/phase4-gate-closing-addendum.md §9).
    expected_gold: dict[str, Any] | None = None
    # Pass-rate over reps required to pass; None takes eval.yaml's
    # default.
    threshold: float | None = None
    xfail: Xfail | None = None
    # Rows with a setup block: fewer scenario-reaching reps than this
    # floor grades the row INCONCLUSIVE — a distinct status, neither
    # pass nor fail, and never XPASS.
    reached_floor: int = Field(default=2, ge=1)
    # Sentinel rows guard only the invariant: any exit in the expected
    # list passes shape, and content assertions apply only at exit 0.
    sentinel: bool = False
    # Rows sharing a route_pair must route identically (first tool).
    route_pair: str | None = None
    # Why this row exists / where its text came from.
    note: str = ""

    @model_validator(mode="after")
    def _single_or_multi(self) -> "BankRow":
        single = self.question is not None
        multi = bool(self.turns)
        if single == multi:
            raise ValueError(
                f"row {self.id}: exactly one of question/turns is required"
            )
        if single and self.expect is None:
            raise ValueError(f"row {self.id}: single-turn rows need expect")
        if multi and self.expect is not None:
            raise ValueError(
                f"row {self.id}: multi-turn rows put expect on each turn"
            )
        if self.expected_gold is not None and self.gold is None:
            raise ValueError(
                f"row {self.id}: expected_gold without a gold script — "
                f"gold values must be produced by executed code"
            )
        needs_gold = {
            a.kind
            for turn_expect in self._expectations()
            for a in turn_expect.assertions
            if a.kind in GOLD_ASSERTION_KINDS
            or (a.kind == "not_contains" and a.from_gold_field is not None)
        }
        if needs_gold and self.gold is None:
            raise ValueError(
                f"row {self.id}: assertions {sorted(needs_gold)} need a "
                f"gold script"
            )
        return self

    def _expectations(self) -> list[Expectation]:
        if self.expect is not None:
            return [self.expect]
        return [turn.expect for turn in self.turns]

    def all_turns(self) -> list[BankTurn]:
        """Uniform view: a single-turn row as a one-item turn list."""
        if self.turns:
            return self.turns
        assert self.question is not None and self.expect is not None
        return [BankTurn(question=self.question, expect=self.expect)]


class EvalConfig(BaseModel):
    """eval.yaml, beside the bank — deliberately NOT pack config: the
    pack must not know it is being examined."""

    model_config = ConfigDict(extra="forbid")

    default_runs: int = 5
    default_threshold: float = 1.0
    # Pack directory, relative to the bank root (or absolute).
    pack: str
    report_dir: str = "reports"


# --- Run report (JSONL) --------------------------------------------------


class LlmStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latencies_ms: list[int] = []


class EmittedTokens(BaseModel):
    """Which optional tokens the draft emitted — the §7.2 requirement:
    stochastic failures (N9/N10) fire only when the draft happens to
    state a path or a prose date, so grade stratifies pass-rates by
    these."""

    model_config = ConfigDict(extra="forbid")

    line_numbers: list[str] = []
    file_paths: list[str] = []
    iso_dates: list[str] = []
    prose_dates: list[str] = []
    money: list[str] = []
    float_tails: list[str] = []
    word_numbers: list[str] = []
    backticked: list[str] = []


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int
    question: str
    conversation_id: int | None = None
    engine_turn: int | None = None
    # None only when ask() raised; error then says why, exit_equiv 1.
    outcome: TurnOutcome | None = None
    exit_equiv: int
    tools_used: list[str] = []
    evidence_ref: str | None = None
    # The canonical bundle JSON, inlined: grade needs the evidence for
    # window/retry/dump checks and must not depend on shipping work.db.
    evidence_payload: str | None = None
    verdict: VerifierVerdict | None = None
    status_events: list[StatusEvent] = []
    substrate_versions: list[str] = []
    wall_ms: int = 0
    llm: LlmStats = LlmStats()
    emitted_tokens: EmittedTokens = EmittedTokens()
    placeholder_failures: list[str] = []
    nudges: int = 0
    # Control verbs the router wrote as text and the harness read as
    # the call anyway (Polish Pass): a model habit worth a number beside
    # the nudges it used to cost, so a pin that changes it shows in the
    # grade, not only in provenance.
    lenient_parses: int = 0
    error: str | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["run"] = "run"
    row_id: str
    rep: int  # 1-based
    started_at: datetime
    wall_ms_total: int = 0
    turns: list[TurnRecord]


class RunReportHeader(BaseModel):
    """First line of every report: the determinism block. Resume and
    grade both refuse when the world under their feet is not the world
    this header names."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["header"] = "header"
    schema_version: int = 1
    engine_sha: str
    engine_dirty: bool
    target_sha: str | None
    seed: int | None
    world_manifests: dict[str, str]
    model: str
    pack: str
    bank_hash: str
    eval_config: EvalConfig
    runs_requested: int
    rows_filter: list[str] | None = None
    started_at: datetime
