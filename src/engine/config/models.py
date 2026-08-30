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

from pydantic import BaseModel, ConfigDict


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
    # Columns with these suffixes must hold values in [0,1] or [0,100].
    rate_column_suffixes: list[str] = ["_rate", "_pct", "_ratio"]
    # An empty result or a lone 0/NULL/false scalar caps the answer at
    # unverified (fix pass 3: the wrong-question shape the 4b baseline
    # found). Off only for a pack whose zero answers are routine.
    challenge_zero_results: bool = True


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
    # opportunity_pct, amount_count. The precedent for a config-owned
    # token list is the verifier's rate_column_suffixes.
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


class DisplaySettings(BaseModel):
    """Presentation knobs shared by every surface that renders an
    answer (CLI text, eval flattening, the browser)."""

    model_config = ConfigDict(extra="forbid")

    money: MoneySettings | None = None


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
