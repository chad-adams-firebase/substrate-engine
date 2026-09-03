"""DI container: turn a loaded pack into constructed adapters.

build() is the only place adapter construction happens at runtime.
The core never instantiates a concrete adapter; it receives resolved
ports from here (CLAUDE.md architecture law).
"""

from typing import Any

from pydantic import ValidationError

from engine.config.models import PortName
from engine.config.pack_loader import LoadedPack
from engine.runtime.registry import AdapterRegistry, default_registry


class AdapterBuildError(Exception):
    """An adapter could not be built from the pack's config. The message
    names the port, the adapter, and what was wrong with the settings."""


class ResolvedPorts:
    """The constructed adapters for one pack, keyed by port. Ports the
    pack did not configure are simply absent (e.g. a pack without the
    logs substrate has no execution_log adapter)."""

    def __init__(self, adapters: dict[PortName, Any]) -> None:
        self._adapters = adapters

    def get(self, port: PortName) -> Any:
        if port not in self._adapters:
            raise KeyError(f"Pack configured no adapter for port '{port}'.")
        return self._adapters[port]

    def configured(self) -> dict[PortName, Any]:
        return dict(self._adapters)


def build(pack: LoadedPack, registry: AdapterRegistry | None = None) -> ResolvedPorts:
    registry = registry if registry is not None else default_registry()
    adapters: dict[PortName, Any] = {}
    for port, selection in pack.config.adapters.items():
        adapters[port] = _create(registry, pack, port)
    return ResolvedPorts(adapters)


def build_port(
    pack: LoadedPack, port: PortName, registry: AdapterRegistry | None = None
) -> Any:
    """One port's configured adapter, for an offline tool that needs a
    single substrate and must not construct the rest (eval exposure
    reads the substrate store and nothing else — no work.db, no LLM).
    Still DI from config: the pack chooses the adapter."""
    if port not in pack.config.adapters:
        raise AdapterBuildError(f"Pack configured no adapter for port '{port}'.")
    registry = registry if registry is not None else default_registry()
    return _create(registry, pack, port)


def _create(registry: AdapterRegistry, pack: LoadedPack, port: PortName) -> Any:
    selection = pack.config.adapters[port]
    try:
        return registry.create(
            port, selection.adapter, selection.settings, pack.root
        )
    except ValidationError as exc:
        details = "; ".join(
            f"{' -> '.join(str(part) for part in err['loc']) or '(root)'}: "
            f"{err['msg']}"
            for err in exc.errors()
        )
        raise AdapterBuildError(
            f"Port '{port}', adapter {selection.adapter!r}: invalid "
            f"settings — {details}"
        ) from exc
