"""Pack configuration models.

A pack is configured by a config.yaml validated against PackConfig.
Substrate and tool names are closed enums taken from Brief §4 and §6:
the tool surface is closed by design, and validating here means a
pack typo fails at load time, not at first use. Later phases fill in
the implementations behind these names; the enums are the contract.

extra="forbid" everywhere: an unknown key in a pack config is a typo,
and typos fail loudly.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SubstrateName(StrEnum):
    """The ten substrates of Brief §4. A pack enables a subset."""

    DATA_DICTIONARY = "data_dictionary"
    DATA_DICTIONARY_MAP = "data_dictionary_map"
    CODE_KNOWLEDGE_GRAPH = "code_knowledge_graph"
    CKG_COMPONENTS = "ckg_components"
    PRIMER = "primer"
    SOURCE_CODE = "source_code"
    APPLICATION_DATABASE = "application_database"
    APPLICATION_LOGS = "application_logs"
    UNIVARIATE_STATISTICS = "univariate_statistics"
    BUSINESS_CONTEXT_DOCS = "business_context_docs"


class ToolName(StrEnum):
    """The closed tool surface of Brief §6."""

    QUERY_UNIVARIATE_STATS = "query_univariate_stats"
    LOOKUP_DATA_DICTIONARY = "lookup_data_dictionary"
    TRAVERSE_CODE_KNOWLEDGE_GRAPH = "traverse_code_knowledge_graph"
    RUN_SQL = "run_sql"
    READ_SOURCE = "read_source"
    ANSWER_FROM_KNOWN_ITEMS = "answer_from_known_items"
    APP_PRIMER = "app_primer"
    SEARCH_BUSINESS_DOCS = "search_business_docs"
    CHECK_EXECUTION = "check_execution"
    APP_CAPABILITIES = "app_capabilities"


class PortName(StrEnum):
    """The ports a pack can select adapters for (Brief §3)."""

    LLM = "llm"
    SQL = "sql"
    SUBSTRATE_STORE = "substrate_store"
    WORK_STORE = "work_store"
    IDENTITY = "identity"
    EXECUTION_LOG = "execution_log"
    SOURCE_CODE = "source_code"


class AdapterSelection(BaseModel):
    """One port's adapter choice: a registry key plus adapter-specific
    settings. Settings are validated against the selected adapter's own
    pydantic model at container-build time, not here — this model does
    not know (and must not know) any concrete adapter."""

    model_config = ConfigDict(extra="forbid")

    adapter: str
    settings: dict[str, Any] = {}


class GenerationConfig(BaseModel):
    """Settings for the substrate generators (Brief §13).

    Everything a generator run could need to tune lives here, never in
    generator code — the work machine tunes by editing the pack. The
    source repo path and pinned SHA are NOT duplicated here: they live
    in adapters.source_code.settings, the single source of truth.
    """

    model_config = ConfigDict(extra="forbid")

    # The target app's SQLite database (input to `engine convert`),
    # pack-relative or absolute.
    source_sqlite: str
    # The seed its simulation ran with. Trusted config — the database
    # cannot prove its own seed — recorded into every manifest as half
    # of the (commit SHA, seed) pinning pair.
    simulation_seed: int
    # Component ids look like "<prefix>.<group>.<slug>".
    component_id_prefix: str
    # Which files the CKG extraction covers, relative to the source
    # repo root.
    source_globs: list[str]
    exclude_globs: list[str] = []
    # Enum data-scan heuristic: a VARCHAR column with at most this many
    # distinct values (all matching an enum-shaped pattern) becomes an
    # enum candidate. Threshold is config because it will be tuned at
    # work against real schemas.
    enum_scan_max_distinct: int = 12


class RunSqlSettings(BaseModel):
    """Knobs for the run_sql tool's execute–check–repair loop (§7)."""

    model_config = ConfigDict(extra="forbid")

    # Repair retries after the first failed execution.
    max_repair_attempts: int = 2
    # Result rows kept in the output table; the pre-truncation count
    # is always preserved (Table.total_row_count).
    max_result_rows: int = 200
    # Named in the grounding prompt so generated SQL targets the
    # dialect the pack's SqlPort adapter actually speaks.
    dialect: str = "duckdb"
    # A COUNT/SUM over a join that can multiply rows draws one repair
    # round (fix pass 3, MT2's fan-out); the model may resend unchanged.
    fan_out_lint: bool = True
    # A WHERE col = 'LITERAL' (or IN (...)) on a column whose dictionary
    # enum never holds the literal draws one repair round naming the
    # observed values (coverage pass, R-A: to_status = 'REJECTED' is not
    # a status); the model may resend unchanged and the override warns.
    enum_literal_lint: bool = True
    # A timestamp difference multiplied or divided by a numeric literal
    # draws one repair round (duration pass, W3 rep 4: AVG(a - b) / 86400
    # is an interval of 0.041667 seconds, not 0.0417 days, and it
    # verified); the model may resend unchanged and the override warns.
    interval_arithmetic_lint: bool = True


class SearchBusinessDocsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = 5


class CheckExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Error events kept in the tool's output/evidence; the port
    # returns everything, the tool truncates visibly.
    max_errors: int = 50
    # Stats columns ("table.column") whose [min, max] anchor the log's
    # data coverage — resolved from the stats substrate at composition,
    # never wall-clock. Empty (default) disables the window guard and
    # the router-prompt coverage line. Naming columns is deliberate: a
    # naive all-TIMESTAMP reduce would drag coverage back to the
    # oldest contract date, years before any log line.
    coverage_columns: list[str] = []
    # A window may overhang resolved coverage by this much before the
    # entirely-outside guard fires (stats snapshots trail the log; the
    # log, not the snapshot, is the source of truth at the edges).
    coverage_grace_days: int = 7


class ToolSettings(BaseModel):
    """Per-tool behavior knobs. Unlike adapter settings (an open
    registry, validated by each adapter), the tool surface is a
    closed enum — so tool settings are typed centrally, and only
    tools with knobs get a block."""

    model_config = ConfigDict(extra="forbid")

    run_sql: RunSqlSettings = RunSqlSettings()
    search_business_docs: SearchBusinessDocsSettings = SearchBusinessDocsSettings()
    check_execution: CheckExecutionSettings = CheckExecutionSettings()


class ContextSettings(BaseModel):
    """What the router sees of a long conversation (Brief §10.3, Phase
    5 Block 4): every turn newer than the running summary verbatim, and
    the summary for the rest. Turn counts, not characters — determin-
    istic and testable. The verbatim window is never shorter than
    last_n_turns and never longer than last_n_turns +
    summary_refresh_after_turns - 1: the summary folds in the turns
    beyond the window once summary_refresh_after_turns of them have
    accumulated, so no turn is ever neither summarized nor shown."""

    model_config = ConfigDict(extra="forbid")

    # The least number of recent turns the router always sees verbatim.
    last_n_turns: int = Field(default=10, ge=1)
    # How many turns must fall past the window before the summary is
    # regenerated (one LLM call, synchronous inside the turn in v1).
    summary_refresh_after_turns: int = Field(default=5, ge=1)
    # The turn count past which the page nudges toward a fresh
    # conversation — a dismissible banner, never a forced boundary.
    nudge_after_turns: int = Field(default=30, ge=1)


class HarnessSettings(BaseModel):
    """Knobs for the Phase 4 agent harness (Brief §8). Every bound the
    graph enforces is pack config, never a constant in graph code."""

    model_config = ConfigDict(extra="forbid")

    # Router iterations per turn; hitting the cap is a first-class
    # refuse outcome, not an error.
    max_router_iterations: int = 6
    # Redraft attempts after a placeholder-resolution failure or a
    # verifier mismatch, before the downgrade path.
    max_draft_retries: int = 2
    # Table rows shown back to the router in tool-result feedback;
    # the drafter and verifier always see the full retained output.
    max_rows_in_context: int = 30
    # The longest single-line string a placeholder may inject into a
    # sentence. Anything longer, or anything multi-line, is a passage
    # — a description, a document snippet, source text — and resolves
    # only inside a fenced code block; mid-sentence it is a resolution
    # failure the drafter retries (Phase 5 Block 2, the play session's
    # O1 text-block injection: whole descriptions pasted inline,
    # snippets cut mid-word). The retry budget is max_draft_retries;
    # when it runs out the passage ships as written rather than
    # costing the answer.
    inline_value_max_chars: int = 120
    # Dictionary columns ("table.column") whose enum values are
    # lifecycle vocabulary — rendered into the router prompt's
    # definitional bullet so "difference between RECEIVED and READY"
    # routes to app_primer, not into run_sql's budget (play pass
    # R1/R3/R5). Empty (default) renders no status values; the
    # mechanism mirrors check_execution's coverage_columns.
    lifecycle_status_columns: list[str] = []
    # The conversation-context window and summary cadence (§10.3).
    context: ContextSettings = ContextSettings()


class JudgeSettings(BaseModel):
    """The verifier's LLM fuzzy judge (§9.2 step 3) — a yes/no call
    for claims mechanical matching cannot settle."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # Judge calls per verification attempt; exhausted budget leaves
    # remaining fuzzy claims unmatched (fail toward the ladder).
    max_calls_per_turn: int = 5
    # Evidence values shown per judge call, ranked by proximity.
    max_candidate_values: int = 10


class PlausibilitySettings(BaseModel):
    """Thresholds for evidence-side sanity checks (§9.3). Defaults fit
    the InvoiceGuard fixture; tuned at work against real distributions,
    which is exactly why they are config."""

    model_config = ConfigDict(extra="forbid")

    # An unfiltered COUNT over one table may deviate this much from the
    # stats snapshot's row_count (the snapshot may trail the live DB).
    row_count_tolerance_pct: float = 10.0
    # A filtered COUNT must not exceed the table's known size.
    enforce_filtered_count_bound: bool = True
    # Non-aggregate result values must lie within stats min/max.
    enforce_min_max_bounds: bool = True
    # Date-shaped results must lie within the stats date range — the
    # data anchor; the verifier never consults the wall clock.
    enforce_date_bounds: bool = True
    # Dates past the stats max are a warning inside this grace window
    # (a slightly stale snapshot), a failure beyond it.
    date_bound_grace_days: int = 7
    # An empty result or a lone 0/NULL/false scalar caps the answer at
    # unverified (fix pass 3: the wrong-question shape the 4b baseline
    # found). Off only for a pack whose zero answers are routine.
    challenge_zero_results: bool = True
    # Aggregate-vs-stats checks over result columns the select-list
    # parse resolves to stats columns (play pass, §9.3 for tables):
    # SUM must not exceed mean × non-null count (cells and the column
    # total; only for columns with a non-negative min, since a
    # filtered subset of a signed column can legitimately exceed the
    # total), and AVG cells must lie within [min, max].
    enforce_aggregate_bounds: bool = True
    # These bounds are impossible-if-clean, so the tolerance absorbs
    # only float slop and stats staleness: outside by up to this much
    # (of the cap, or of the [min,max] span for AVG) warns — the
    # answer ships [UNVERIFIED]; beyond it fails — the answer is
    # refused.
    aggregate_bound_tolerance_pct: float = 10.0
    # Row cap for the alias-resolved per-cell min/max checks in
    # aggregate queries (group keys and passthrough columns).
    aggregate_cell_sample_rows: int = 50
    # A count-shaped result over a multi-table query has no single
    # row_count to equal, but it cannot honestly exceed the largest
    # queried table: a filter only lowers a count, and only a fanning
    # join raises it (pin pass, MT2: 107,509 over a 6,042-row table,
    # 17.8×). Factors are relative to the largest queried table's
    # row_count, so single-table counts and honest line-grain joins
    # pass. Above the warn factor the answer ships [UNVERIFIED];
    # above the fail factor it is refused.
    enforce_joined_count_bound: bool = True
    joined_count_warn_factor: float = 1.0
    joined_count_fail_factor: float = 3.0
    # The entity-count bound (guard pass, AMB2): a COUNT column whose
    # alias names an entity the stats substrate knows as a table —
    # invoice_count, total_invoices, supplier_count — cannot exceed
    # that table's row_count, whatever the statement reads. AMB2's
    # `COUNT(*) AS invoice_count FROM invoice_history` returned 6,432
    # against 1,990 invoices in existence and passed every bound,
    # because nothing tied the alias's noun to a table. Warn only: an
    # alias is a naming convention, not a type, so the badge comes off
    # and the answer ships [UNVERIFIED]. The noun resolves by a stem
    # rule against the stats tables (singular/plural), never a list in
    # engine code; an alias with no count affix, or whose noun matches
    # no table (or several), is silent. Tolerance shares
    # row_count_tolerance_pct.
    enforce_entity_count_bound: bool = True
    # A rate-hinted result cell that is exactly 0.0 or 1.0 (100.0 on a
    # percent-scale column) loses the verified badge — warn only, never
    # fail, since saturated rates can be legitimate (pin pass, S2: AVG
    # over a NULL-padded indicator saturates to exactly 1.0). A
    # count-like cell in the same row below the minimum basis
    # suppresses the warn: tiny populations saturate honestly. Which
    # columns are rates, and at what scale, is the table's own
    # ColumnFormat hint (display.rate) — the same resolution every
    # renderer uses, so a bound never disagrees with the digits shown.
    challenge_saturated_rates: bool = True
    saturated_rate_min_basis: int = 20
    # The duration class's floor (duration pass, W3 rep 4: AVG over an
    # interval scaled by 86400 humanized to "0 seconds" and verified).
    # A duration-hinted AGGREGATE cell below one second loses the
    # verified badge — warn only, never fail, since an instant
    # transition is a legitimate zero. A count-like cell in the same
    # row below the minimum basis suppresses the warn, as with
    # saturated rates: a tiny population can be instant. Which columns
    # are durations, and in what unit, is the table's own ColumnFormat
    # hint (display.duration) — the same resolution every renderer
    # uses. When the select-list parse cannot classify the item (a
    # CASE wrapper; EPOCH/DATE_DIFF/JULIAN forms are classified since
    # the guard pass), the aggregate is read lexically: a false
    # positive costs a badge, a false negative is the verified zero
    # the play session already met once.
    challenge_degenerate_durations: bool = True
    degenerate_duration_min_basis: int = 20
    # The duration class's ceiling: a duration cell longer than the
    # span between the earliest and latest timestamp in the queried
    # tables (the stats substrate's min/max on their timestamp
    # columns) cannot be an average, a minimum, a maximum or one row's
    # elapsed time — fail. A SUM over a population may honestly exceed
    # the span and is exempt; an item the parse cannot classify warns
    # instead of failing, since the parse cannot rule out a SUM there.
    enforce_duration_span_bound: bool = True


class VerifierSettings(BaseModel):
    """The Verifier's ladder and matching bounds (Brief §9)."""

    model_config = ConfigDict(extra="forbid")

    # Regenerations after a faithfulness mismatch before downgrading.
    max_regenerate_retries: int = 1
    # Where the ladder lands when retries are exhausted: label the
    # answer unverified (default) or refuse outright.
    unmatched_final: Literal["unverified", "refuse"] = "unverified"
    # Relative tolerance for exact numeric matches (float round-trips).
    numeric_rel_tolerance: float = 1e-9
    # Candidate-pair cap for ratio/difference derivations; past it the
    # claim falls to the judge. A derivation engine that can derive
    # anything verifies nothing.
    max_derivation_pairs: int = 200
    judge: JudgeSettings = JudgeSettings()
    plausibility: PlausibilitySettings = PlausibilitySettings()


class MoneySettings(BaseModel):
    """How money renders, and which result columns count as money
    beyond the Dictionary Map's own list (§10.5; NP3). The symbol is
    locale/branding, so it is config with no engine default — a pack
    that declares no money block gets no money formatting at all."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    # fnmatch globs over result column names (LLM-chosen aliases), an
    # explicit extension of the map's column list: e.g. "*_usd".
    column_patterns: list[str] = []
    # An alias that ends in a money column's name is money — SUM(x) AS
    # total_opportunity — unless one of its tokens says otherwise:
    # opportunity_pct, amount_count. A config-owned token list, like
    # the duration and rate alias globs beside it.
    non_money_markers: list[str] = [
        "count", "n", "num", "pct", "percent", "rate", "ratio", "rank",
        "share",
    ]


class UiSettings(BaseModel):
    """What the web layer shows that is the pack's, not the engine's
    (Brief §10.1): name, accent, starter prompts. Empty app_name falls
    back to the pack name; empty accent keeps the stylesheet's neutral."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = ""
    accent_color: str = ""
    starter_prompts: list[str] = []
    # Short pack-authored text answering "what can I ask you?" — the
    # app_capabilities tool's whole evidence (play pass R6: meta
    # questions answer instead of refusing). The LLM phrases it;
    # the text is the grounding.
    capabilities: str = ""


DurationUnit = Literal["seconds", "minutes", "hours", "days"]


class DurationSettings(BaseModel):
    """Which result columns are elapsed times, and what unit their
    numbers count (§10.5; the play session's 1.0806402437502474 days
    and 1:00:00). Durations are almost always computed aliases
    (julianday differences, strftime output), so — unlike money — the
    hint comes from alias globs alone: each list names the aliases
    whose numeric cells are measured in that unit. A column whose
    cells are H:MM:SS strings carries its own unit; list it under
    `clock`. A pack that declares no duration block gets no duration
    formatting at all."""

    model_config = ConfigDict(extra="forbid")

    days: list[str] = []
    hours: list[str] = []
    minutes: list[str] = []
    seconds: list[str] = []
    clock: list[str] = []

    def unit_patterns(self) -> list[tuple[DurationUnit | None, list[str]]]:
        """(unit, globs) in match order — the first unit whose glob
        fits an alias wins."""
        return [
            ("days", self.days),
            ("hours", self.hours),
            ("minutes", self.minutes),
            ("seconds", self.seconds),
            (None, self.clock),
        ]


RateScale = Literal["fraction", "percent"]


class RateSettings(BaseModel):
    """Which result columns are rates, and at what scale (§10.5; the
    coverage pass's third hint kind — Play Session #2 read
    0.9221105527638191 where a manager reads 92.2%). Rates are computed
    aliases, so — like durations — the hint comes from alias globs
    alone. `fraction` lists aliases whose cells are 0–1 and show as a
    percentage (flag_rate 0.9221 -> 92.2%); `percent` lists aliases
    whose cells were already multiplied by 100 (flag_pct 92.21 ->
    92.2%). The scale is the alias author's word, and the Verifier's
    rate bounds and saturation checks read the same hint: a percent
    cell is bounded on 0–100 and saturates at 100.0. A pack that
    declares no rate block gets no rate formatting and no rate bounds."""

    model_config = ConfigDict(extra="forbid")

    fraction: list[str] = []
    percent: list[str] = []

    def scale_patterns(self) -> list[tuple[RateScale, list[str]]]:
        """(scale, globs) in match order — the first scale whose glob
        fits an alias wins."""
        return [("fraction", self.fraction), ("percent", self.percent)]


class DisplaySettings(BaseModel):
    """Presentation knobs shared by every surface that renders an
    answer (CLI text, eval flattening, the browser)."""

    model_config = ConfigDict(extra="forbid")

    money: MoneySettings | None = None
    duration: DurationSettings | None = None
    rate: RateSettings | None = None


class PackConfig(BaseModel):
    """The validated shape of a pack's config.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    substrates: list[SubstrateName]
    tools: list[ToolName]
    adapters: dict[PortName, AdapterSelection]
    tool_settings: ToolSettings = ToolSettings()
    harness: HarnessSettings = HarnessSettings()
    verifier: VerifierSettings = VerifierSettings()
    display: DisplaySettings = DisplaySettings()
    ui: UiSettings = UiSettings()
    generation: GenerationConfig | None = None
