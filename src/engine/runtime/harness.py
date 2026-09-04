"""Composition root for the ask path: wire pack + ports + registry
into an AskSession. Mirrors runtime/tools.py — only runtime may name
concrete pieces; the harness and verifier take everything injected.
"""

from engine.config.models import PortName, ToolName
from engine.config.pack_loader import LoadedPack
from engine.harness.drafter import Drafter
from engine.harness.events import StatusListener
from engine.harness.graph import GraphDeps
from engine.harness.prompts import (
    render_drafter_prompt,
    render_router_prompt,
    render_summarizer_prompt,
)
from engine.harness.session import AskSession
from engine.runtime.container import ResolvedPorts
from engine.runtime.tools import (
    resolve_data_terms,
    resolve_definitional_terms,
    resolve_entity_catalog,
    resolve_interpretation_terms,
    resolve_pack_coverage,
)
from engine.tools.registry import ToolRegistry
from engine.verifier.checks import CheckRegistry, default_checks
from engine.verifier.verify import Verifier


def build_verifier(pack: LoadedPack, ports: ResolvedPorts) -> Verifier:
    store = ports.get(PortName.SUBSTRATE_STORE)
    return Verifier(
        CheckRegistry(default_checks()),
        ports.get(PortName.LLM),
        pack.config.verifier,
        stats_provider=store.stats,
        catalog=resolve_entity_catalog(pack, ports),
    )


def build_harness(
    pack: LoadedPack,
    ports: ResolvedPorts,
    registry: ToolRegistry,
    listener: StatusListener | None = None,
) -> AskSession:
    llm = ports.get(PortName.LLM)
    coverage = resolve_pack_coverage(pack, ports)
    deps = GraphDeps(
        llm=llm,
        registry=registry,
        verifier=build_verifier(pack, ports),
        drafter=Drafter(
            llm,
            render_drafter_prompt(
                app_name=pack.config.name,
                interpretation_terms=resolve_interpretation_terms(pack, ports),
            ),
            inline_value_max_chars=pack.config.harness.inline_value_max_chars,
        ),
        settings=pack.config.harness,
        catalog=resolve_entity_catalog(pack, ports),
        summarizer_prompt=render_summarizer_prompt(app_name=pack.config.name),
        router_prompt=render_router_prompt(
            app_name=pack.config.name,
            app_description=pack.config.description,
            max_iterations=pack.config.harness.max_router_iterations,
            data_coverage=(
                (coverage.start.isoformat(), coverage.end.isoformat())
                if coverage is not None
                else None
            ),
            data_terms=resolve_data_terms(pack, ports),
            definitional_terms=resolve_definitional_terms(pack, ports),
            has_capabilities_tool=ToolName.APP_CAPABILITIES
            in pack.config.tools,
        ),
    )
    return AskSession(
        deps=deps,
        work_store=ports.get(PortName.WORK_STORE),
        identity=ports.get(PortName.IDENTITY),
        listener=listener,
    )
