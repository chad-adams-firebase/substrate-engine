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


class PackConfig(BaseModel):
    """The validated shape of a pack's config.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    substrates: list[SubstrateName]
    tools: list[ToolName]
    adapters: dict[PortName, AdapterSelection]
