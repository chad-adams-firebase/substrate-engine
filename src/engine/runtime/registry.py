"""Adapter registry: per port, a map of adapter name -> factory.

This module and the container are the composition root — the only
code in the engine allowed to import concrete adapters. Everything
else sees ports.

A factory takes the pack's raw settings dict plus the pack root
(so path-shaped settings resolve relative to the pack directory) and
returns a constructed adapter. Each factory validates the settings
against its adapter's own pydantic model, so a bad pack config fails
at build time with a message naming the offending field.

Swapping which adapter a pack uses is a config.yaml edit, never a
code edit: the pack names a registry key, and registration is the
only code that knows the mapping. Tests register extra adapters
through register() to prove exactly that.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from engine.adapters.identity_fake import FakeUserIdentity, FakeUserSettings
from engine.adapters.llm_openrouter import OpenRouterLLM, OpenRouterSettings
from engine.adapters.source_code_local import LocalDirectorySource, LocalSourceSettings
from engine.adapters.sql_duckdb import DuckDbSettings, DuckDbSql
from engine.adapters.work_store_sqlite import SqliteWorkStore, SqliteWorkStoreSettings
from engine.config.models import PortName

AdapterFactory = Callable[[dict[str, Any], Path], Any]


class UnknownAdapterError(Exception):
    """The pack asked for an adapter name nobody registered."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[PortName, dict[str, AdapterFactory]] = {}

    def register(self, port: PortName, name: str, factory: AdapterFactory) -> None:
        self._factories.setdefault(port, {})[name] = factory

    def create(
        self, port: PortName, name: str, settings: dict[str, Any], pack_root: Path
    ) -> Any:
        by_name = self._factories.get(port, {})
        if name not in by_name:
            known = ", ".join(sorted(by_name)) or "(none)"
            raise UnknownAdapterError(
                f"No adapter named {name!r} is registered for port "
                f"'{port}'. Registered: {known}."
            )
        return by_name[name](settings, pack_root)


def _pack_path(value: str, pack_root: Path) -> str:
    """Resolve a path-shaped setting relative to the pack directory,
    leaving ":memory:" and absolute paths untouched."""
    if value == ":memory:" or Path(value).is_absolute():
        return value
    return str(pack_root / value)


def _make_openrouter(settings: dict[str, Any], pack_root: Path) -> OpenRouterLLM:
    return OpenRouterLLM(OpenRouterSettings.model_validate(settings))


def _make_duckdb(settings: dict[str, Any], pack_root: Path) -> DuckDbSql:
    validated = DuckDbSettings.model_validate(settings)
    resolved = validated.model_copy(
        update={"database": _pack_path(validated.database, pack_root)}
    )
    return DuckDbSql(resolved)


def _make_sqlite_work_store(
    settings: dict[str, Any], pack_root: Path
) -> SqliteWorkStore:
    validated = SqliteWorkStoreSettings.model_validate(settings)
    resolved = validated.model_copy(
        update={"database": _pack_path(validated.database, pack_root)}
    )
    return SqliteWorkStore(resolved)


def _make_fake_user(settings: dict[str, Any], pack_root: Path) -> FakeUserIdentity:
    return FakeUserIdentity(FakeUserSettings.model_validate(settings))


def _make_local_source(
    settings: dict[str, Any], pack_root: Path
) -> LocalDirectorySource:
    validated = LocalSourceSettings.model_validate(settings)
    resolved = validated.model_copy(
        update={"root": _pack_path(validated.root, pack_root)}
    )
    return LocalDirectorySource(resolved)


def default_registry() -> AdapterRegistry:
    """The Phase 1 local adapters. Real adapters (Databricks FM,
    databricks-sql-connector, Delta, Splunk, git clone) register here
    in a later phase."""
    registry = AdapterRegistry()
    registry.register(PortName.LLM, "openrouter", _make_openrouter)
    registry.register(PortName.SQL, "duckdb", _make_duckdb)
    registry.register(PortName.WORK_STORE, "sqlite", _make_sqlite_work_store)
    registry.register(PortName.IDENTITY, "fake_user", _make_fake_user)
    registry.register(PortName.SOURCE_CODE, "local_directory", _make_local_source)
    return registry
