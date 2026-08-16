"""Pack loader: a valid pack loads; every failure mode produces its own
legible, pack-author-actionable error."""

import copy

import pytest

from engine.config.models import PortName, SubstrateName, ToolName
from engine.config.pack_loader import PackLoadError, load_pack
from tests.conftest import VALID_CONFIG


def test_valid_pack_loads(make_pack):
    pack_dir = make_pack(VALID_CONFIG)

    pack = load_pack(pack_dir)

    assert pack.config.name == "testpack"
    assert pack.root == pack_dir
    assert SubstrateName.DATA_DICTIONARY in pack.config.substrates
    assert ToolName.RUN_SQL in pack.config.tools
    assert pack.config.adapters[PortName.LLM].adapter == "openrouter"


def test_missing_directory_is_legible(tmp_path):
    with pytest.raises(PackLoadError, match="does not exist"):
        load_pack(tmp_path / "nope")


def test_missing_config_yaml_is_legible(tmp_path):
    (tmp_path / "empty_pack").mkdir()
    with pytest.raises(PackLoadError, match="no config.yaml"):
        load_pack(tmp_path / "empty_pack")


def test_invalid_yaml_is_legible(tmp_path):
    pack_dir = tmp_path / "badyaml"
    pack_dir.mkdir()
    (pack_dir / "config.yaml").write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(PackLoadError, match="not valid YAML"):
        load_pack(pack_dir)


def test_non_mapping_yaml_is_legible(tmp_path):
    pack_dir = tmp_path / "listyaml"
    pack_dir.mkdir()
    (pack_dir / "config.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(PackLoadError, match="mapping at the top level"):
        load_pack(pack_dir)


def test_unknown_substrate_rejected(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["substrates"].append("data_dictionnary")  # typo
    with pytest.raises(PackLoadError, match="substrates"):
        load_pack(make_pack(config))


def test_unknown_tool_rejected(make_pack):
    """The tool surface is closed (Brief §6): a name outside the enum is
    a load-time error, not a runtime surprise."""
    config = copy.deepcopy(VALID_CONFIG)
    config["tools"].append("run_spl")
    with pytest.raises(PackLoadError, match="tools"):
        load_pack(make_pack(config))


def test_unknown_port_rejected(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"]["telemetry"] = {"adapter": "x"}
    with pytest.raises(PackLoadError, match="adapters"):
        load_pack(make_pack(config))


def test_extra_top_level_key_rejected(make_pack):
    """extra="forbid": unknown keys are typos and fail loudly."""
    config = copy.deepcopy(VALID_CONFIG)
    config["substrate"] = ["data_dictionary"]  # singular typo
    with pytest.raises(PackLoadError, match="substrate"):
        load_pack(make_pack(config))
