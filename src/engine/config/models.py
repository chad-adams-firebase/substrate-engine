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
from typing import Any

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


class SearchBusinessDocsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = 5


class CheckExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Error events kept in the tool's output/evidence; the port
    # returns everything, the tool truncates visibly.
    max_errors: int = 50


class ToolSettings(BaseModel):
    """Per-tool behavior knobs. Unlike adapter settings (an open
    registry, validated by each adapter), the tool surface is a
    closed enum — so tool settings are typed centrally, and only
    tools with knobs get a block."""

    model_config = ConfigDict(extra="forbid")

    run_sql: RunSqlSettings = RunSqlSettings()
    search_business_docs: SearchBusinessDocsSettings = SearchBusinessDocsSettings()
    check_execution: CheckExecutionSettings = CheckExecutionSettings()


class PackConfig(BaseModel):
    """The validated shape of a pack's config.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    substrates: list[SubstrateName]
    tools: list[ToolName]
    adapters: dict[PortName, AdapterSelection]
    tool_settings: ToolSettings = ToolSettings()
    generation: GenerationConfig | None = None
