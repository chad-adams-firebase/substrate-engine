"""Tool composition: pack config -> constructed ToolRegistry.

Part of the composition root: this is where enabled tool names meet
concrete port instances. Dependencies are validated up front — a pack
enabling check_execution without an execution_log adapter is a config
typo, and typos fail at build time with the missing piece named, not
at first use (the same doctrine as the pack loader's closed enums).
"""

from engine.config.models import PortName, SubstrateName, ToolName
from engine.config.pack_loader import LoadedPack
from engine.runtime.container import ResolvedPorts
from engine.tools.answer_from_known_items import AnswerFromKnownItems
from engine.tools.app_capabilities import AppCapabilities
from engine.tools.app_primer import AppPrimer
from engine.tools.base import Tool
from engine.tools.check_execution import CheckExecution
from engine.tools.coverage import CoverageWindow, resolve_coverage_window
from engine.tools.lookup_data_dictionary import LookupDataDictionary
from engine.tools.query_univariate_stats import QueryUnivariateStats
from engine.tools.read_source import ReadSource
from engine.tools.registry import ToolRegistry
from engine.tools.run_sql import RunSql
from engine.tools.search_business_docs import SearchBusinessDocs
from engine.tools.traverse_code_knowledge_graph import TraverseCodeKnowledgeGraph


class ToolBuildError(Exception):
    """An enabled tool's dependencies are not satisfied by the pack.
    The message names the tool and the missing substrate or adapter."""


REQUIRED_SUBSTRATES: dict[ToolName, set[SubstrateName]] = {
    ToolName.QUERY_UNIVARIATE_STATS: {SubstrateName.UNIVARIATE_STATISTICS},
    ToolName.LOOKUP_DATA_DICTIONARY: {SubstrateName.DATA_DICTIONARY},
    ToolName.TRAVERSE_CODE_KNOWLEDGE_GRAPH: {
        SubstrateName.CODE_KNOWLEDGE_GRAPH,
        SubstrateName.CKG_COMPONENTS,
    },
    ToolName.READ_SOURCE: {
        SubstrateName.CODE_KNOWLEDGE_GRAPH,
        SubstrateName.SOURCE_CODE,
    },
    ToolName.RUN_SQL: {
        SubstrateName.DATA_DICTIONARY,
        SubstrateName.DATA_DICTIONARY_MAP,
        SubstrateName.UNIVARIATE_STATISTICS,
        SubstrateName.APPLICATION_DATABASE,
    },
    ToolName.APP_PRIMER: {SubstrateName.PRIMER, SubstrateName.CKG_COMPONENTS},
    ToolName.SEARCH_BUSINESS_DOCS: {SubstrateName.BUSINESS_CONTEXT_DOCS},
    ToolName.CHECK_EXECUTION: {SubstrateName.APPLICATION_LOGS},
    ToolName.ANSWER_FROM_KNOWN_ITEMS: set(),
    # Answers from pack config (ui.*) alone — no substrate, no port.
    ToolName.APP_CAPABILITIES: set(),
}

REQUIRED_PORTS: dict[ToolName, set[PortName]] = {
    ToolName.QUERY_UNIVARIATE_STATS: {PortName.SUBSTRATE_STORE},
    ToolName.LOOKUP_DATA_DICTIONARY: {PortName.SUBSTRATE_STORE},
    ToolName.TRAVERSE_CODE_KNOWLEDGE_GRAPH: {PortName.SUBSTRATE_STORE},
    ToolName.READ_SOURCE: {PortName.SUBSTRATE_STORE, PortName.SOURCE_CODE},
    ToolName.RUN_SQL: {
        PortName.SUBSTRATE_STORE,
        PortName.SQL,
        PortName.LLM,
        PortName.IDENTITY,
    },
    ToolName.APP_PRIMER: {PortName.SUBSTRATE_STORE},
    ToolName.SEARCH_BUSINESS_DOCS: {PortName.SUBSTRATE_STORE},
    ToolName.CHECK_EXECUTION: {PortName.EXECUTION_LOG},
    ToolName.ANSWER_FROM_KNOWN_ITEMS: {PortName.WORK_STORE},
    ToolName.APP_CAPABILITIES: set(),
}


def _validate(pack: LoadedPack, ports: ResolvedPorts) -> None:
    enabled_substrates = set(pack.config.substrates)
    configured_ports = set(ports.configured())
    problems: list[str] = []
    for tool in pack.config.tools:
        for substrate in sorted(REQUIRED_SUBSTRATES[tool] - enabled_substrates):
            problems.append(
                f"tool '{tool}' requires substrate '{substrate}', which the "
                f"pack does not enable"
            )
        for port in sorted(REQUIRED_PORTS[tool] - configured_ports):
            problems.append(
                f"tool '{tool}' requires an adapter for port '{port}', which "
                f"the pack does not configure"
            )
    if problems:
        raise ToolBuildError(
            "Pack tool configuration is unsatisfiable:\n  "
            + "\n  ".join(problems)
        )


def resolve_pack_coverage(
    pack: LoadedPack, ports: ResolvedPorts
) -> CoverageWindow | None:
    """The pack's data-coverage window, from the stats substrate via
    the columns named in check_execution settings; None when the pack
    names none. The dependency is conditional (the static REQUIRED_*
    tables stay unconditional), so it is checked here, loudly."""
    columns = pack.config.tool_settings.check_execution.coverage_columns
    if not columns:
        return None
    if SubstrateName.UNIVARIATE_STATISTICS not in pack.config.substrates:
        raise ToolBuildError(
            "check_execution coverage_columns require the "
            "univariate_statistics substrate, which the pack does not "
            "enable"
        )
    if PortName.SUBSTRATE_STORE not in set(ports.configured()):
        raise ToolBuildError(
            "check_execution coverage_columns require an adapter for "
            "port 'substrate_store', which the pack does not configure"
        )
    try:
        return resolve_coverage_window(
            ports.get(PortName.SUBSTRATE_STORE).stats(), columns
        )
    except ValueError as exc:
        raise ToolBuildError(f"check_execution coverage: {exc}") from exc


def resolve_data_terms(
    pack: LoadedPack, ports: ResolvedPorts
) -> list[str] | None:
    """The Dictionary Map's concept names, concept synonyms, and
    metric names — the business vocabulary the router prompt ties to
    run_sql (Addendum N6: "savings" must route like "fires the most").
    None when the pack configures no substrate store or no map."""
    from engine.ports.substrate_store import SubstrateStoreError

    if PortName.SUBSTRATE_STORE not in set(ports.configured()):
        return None
    try:
        mapping = ports.get(PortName.SUBSTRATE_STORE).dictionary_map()
    except SubstrateStoreError:
        return None
    terms: list[str] = []
    for concept in mapping.concepts:
        terms.append(concept.name)
        terms.extend(concept.synonyms)
    for metric in mapping.metrics:
        terms.append(metric.name)
        terms.extend(metric.synonyms)
    deduped = list(dict.fromkeys(terms))
    return deduped or None


def resolve_definitional_terms(
    pack: LoadedPack, ports: ResolvedPorts
) -> list[str] | None:
    """The vocabulary of definitional/lifecycle questions (play pass
    B1, the N6 shape's second application): primer component names,
    lifecycle status values from pack-declared dictionary columns, and
    Dictionary Map concept names + synonyms. The same concept words
    appear in resolve_data_terms — deliberately: a business phrasing
    of a data question is run_sql's, while "what does X mean" is a
    definition. The engine renders whatever the pack declares; None
    when nothing resolves."""
    from engine.ports.substrate_store import SubstrateStoreError

    if PortName.SUBSTRATE_STORE not in set(ports.configured()):
        return None
    store = ports.get(PortName.SUBSTRATE_STORE)
    terms: list[str] = []
    try:
        for component in store.components():
            terms.append(component.name)
    except SubstrateStoreError:
        pass
    status_columns = set(pack.config.harness.lifecycle_status_columns)
    if status_columns:
        try:
            for row in store.dictionary():
                qualified = f"{row.table_name}.{row.column_name}"
                if qualified in status_columns and row.enum_values:
                    terms.extend(row.enum_values)
        except SubstrateStoreError:
            pass
    try:
        for concept in store.dictionary_map().concepts:
            terms.append(concept.name)
            terms.extend(concept.synonyms)
    except SubstrateStoreError:
        pass
    deduped = list(dict.fromkeys(terms))
    return deduped or None


def resolve_interpretation_terms(
    pack: LoadedPack, ports: ResolvedPorts
) -> str | None:
    """Rendered lines for every map entry that declares
    interpretations (play pass C4/W8) — the drafter rule's payload.
    None when nothing declares any."""
    from engine.ports.substrate_store import SubstrateStoreError

    if PortName.SUBSTRATE_STORE not in set(ports.configured()):
        return None
    try:
        mapping = ports.get(PortName.SUBSTRATE_STORE).dictionary_map()
    except SubstrateStoreError:
        return None
    lines: list[str] = []
    for entry in [*mapping.concepts, *mapping.metrics]:
        if entry.interpretations:
            readings = "; ".join(
                f"{i.name} ({i.meaning})" for i in entry.interpretations
            )
            # The synonyms ride along (Polish Pass): the rule says
            # "when your answer uses one of them", and a drafter that
            # wrote "savings realized" did not connect the phrase to
            # recovered_opportunity until the line said so.
            term = entry.name
            if entry.synonyms:
                term += f" (also: {', '.join(entry.synonyms)})"
            lines.append(f"  {term}: {readings}")
    return "\n".join(lines) or None


def build_tools(pack: LoadedPack, ports: ResolvedPorts) -> ToolRegistry:
    _validate(pack, ports)
    settings = pack.config.tool_settings
    enabled = set(pack.config.tools)
    tools: list[Tool] = []

    def store():
        return ports.get(PortName.SUBSTRATE_STORE)

    if ToolName.QUERY_UNIVARIATE_STATS in enabled:
        tools.append(QueryUnivariateStats(store()))
    if ToolName.LOOKUP_DATA_DICTIONARY in enabled:
        tools.append(
            LookupDataDictionary(
                store(),
                include_map=SubstrateName.DATA_DICTIONARY_MAP
                in pack.config.substrates,
            )
        )
    if ToolName.TRAVERSE_CODE_KNOWLEDGE_GRAPH in enabled:
        tools.append(TraverseCodeKnowledgeGraph(store()))
    if ToolName.READ_SOURCE in enabled:
        tools.append(ReadSource(store(), ports.get(PortName.SOURCE_CODE)))
    if ToolName.RUN_SQL in enabled:
        tools.append(
            RunSql(
                store(),
                ports.get(PortName.SQL),
                ports.get(PortName.LLM),
                ports.get(PortName.IDENTITY),
                settings.run_sql,
                display=pack.config.display,
            )
        )
    if ToolName.APP_PRIMER in enabled:
        tools.append(AppPrimer(store()))
    if ToolName.SEARCH_BUSINESS_DOCS in enabled:
        tools.append(SearchBusinessDocs(store(), settings.search_business_docs))
    if ToolName.CHECK_EXECUTION in enabled:
        tools.append(
            CheckExecution(
                ports.get(PortName.EXECUTION_LOG),
                settings.check_execution,
                coverage=resolve_pack_coverage(pack, ports),
            )
        )
    if ToolName.ANSWER_FROM_KNOWN_ITEMS in enabled:
        tools.append(AnswerFromKnownItems(ports.get(PortName.WORK_STORE)))
    if ToolName.APP_CAPABILITIES in enabled:
        tools.append(AppCapabilities(pack.config.ui))
    return ToolRegistry(tools)
