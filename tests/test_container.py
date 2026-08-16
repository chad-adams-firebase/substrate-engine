"""DI container: adapters are chosen by config alone, settings are
validated against the selected adapter's model, and failures are
legible."""

import copy

import pytest

from engine.adapters.identity_fake import FakeUserIdentity
from engine.adapters.sql_duckdb import DuckDbSql
from engine.config.models import PortName
from engine.config.pack_loader import load_pack
from engine.runtime.container import AdapterBuildError, build
from engine.runtime.registry import AdapterRegistry, UnknownAdapterError
from tests.conftest import VALID_CONFIG


def test_builds_every_configured_port(make_pack):
    pack = load_pack(make_pack(VALID_CONFIG))

    ports = build(pack)

    resolved = ports.configured()
    assert set(resolved) == {
        PortName.LLM,
        PortName.SQL,
        PortName.WORK_STORE,
        PortName.IDENTITY,
        PortName.SOURCE_CODE,
    }
    assert isinstance(resolved[PortName.SQL], DuckDbSql)
    assert isinstance(resolved[PortName.IDENTITY], FakeUserIdentity)


def test_unconfigured_port_is_absent_and_get_raises(make_pack):
    pack = load_pack(make_pack(VALID_CONFIG))
    ports = build(pack)

    with pytest.raises(KeyError, match="execution_log"):
        ports.get(PortName.EXECUTION_LOG)


class _VariantA:
    def __init__(self) -> None:
        self.kind = "A"


class _VariantB:
    def __init__(self) -> None:
        self.kind = "B"


def test_config_edit_swaps_adapter_without_code_change(make_pack):
    """The Phase 1 acceptance criterion: changing the adapter NAME in
    config.yaml changes what is instantiated — same registry, same
    code, different config."""
    registry = AdapterRegistry()
    registry.register(PortName.IDENTITY, "variant_a", lambda s, root: _VariantA())
    registry.register(PortName.IDENTITY, "variant_b", lambda s, root: _VariantB())

    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"] = {"identity": {"adapter": "variant_a"}}
    pack_a = load_pack(make_pack(config, name="pack_a"))

    config["adapters"]["identity"]["adapter"] = "variant_b"  # the only change
    pack_b = load_pack(make_pack(config, name="pack_b"))

    assert isinstance(build(pack_a, registry).get(PortName.IDENTITY), _VariantA)
    assert isinstance(build(pack_b, registry).get(PortName.IDENTITY), _VariantB)


def test_unknown_adapter_name_lists_registered_ones(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"]["llm"]["adapter"] = "opnrouter"  # typo
    pack = load_pack(make_pack(config))

    with pytest.raises(UnknownAdapterError, match="openrouter"):
        build(pack)


def test_invalid_settings_name_port_adapter_and_field(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    del config["adapters"]["llm"]["settings"]["model"]  # required field
    pack = load_pack(make_pack(config))

    with pytest.raises(AdapterBuildError) as excinfo:
        build(pack)
    message = str(excinfo.value)
    assert "llm" in message
    assert "openrouter" in message
    assert "model" in message


def test_extra_setting_key_rejected(make_pack):
    """Adapter settings models are extra="forbid" too."""
    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"]["identity"]["settings"]["usernme"] = "typo"
    pack = load_pack(make_pack(config))

    with pytest.raises(AdapterBuildError, match="usernme"):
        build(pack)


def test_relative_paths_resolve_against_pack_root(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"]["work_store"]["settings"]["database"] = "state/work.db"
    pack = load_pack(make_pack(config))

    store = build(pack).get(PortName.WORK_STORE)

    assert store.settings.database == str(pack.root / "state" / "work.db")
