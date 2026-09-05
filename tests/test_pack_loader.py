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


def test_tool_settings_default_and_override(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    pack = load_pack(make_pack(config))
    assert pack.config.tool_settings.run_sql.max_repair_attempts == 2

    config["tool_settings"] = {"run_sql": {"max_repair_attempts": 5}}
    pack = load_pack(make_pack(config, name="pack2"))
    assert pack.config.tool_settings.run_sql.max_repair_attempts == 5
    # Unspecified blocks keep their defaults.
    assert pack.config.tool_settings.search_business_docs.top_k == 5


def test_tool_settings_typo_rejected(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["tool_settings"] = {"run_sql": {"max_repair_attemps": 5}}  # typo
    with pytest.raises(PackLoadError, match="max_repair_attemps"):
        load_pack(make_pack(config))


def test_harness_and_verifier_settings_default_and_override(make_pack):
    """Phase 3 packs (no harness/verifier blocks) keep loading; every
    Phase 4 bound is pack config with a default."""
    config = copy.deepcopy(VALID_CONFIG)
    pack = load_pack(make_pack(config))
    assert pack.config.harness.max_router_iterations == 6
    assert pack.config.verifier.max_regenerate_retries == 1
    assert pack.config.verifier.unmatched_final == "unverified"
    assert pack.config.verifier.judge.enabled is True
    assert pack.config.verifier.plausibility.row_count_tolerance_pct == 10.0

    config["harness"] = {"max_router_iterations": 3}
    config["verifier"] = {
        "unmatched_final": "refuse",
        "judge": {"max_calls_per_turn": 2},
        "plausibility": {
            "date_bound_grace_days": 0,
            "enforce_duration_span_bound": False,
            "degenerate_duration_min_basis": 5,
        },
    }
    pack = load_pack(make_pack(config, name="pack2"))
    assert pack.config.harness.max_router_iterations == 3
    # Unspecified knobs keep their defaults within an overridden block.
    assert pack.config.harness.max_draft_retries == 2
    assert pack.config.verifier.unmatched_final == "refuse"
    assert pack.config.verifier.judge.max_calls_per_turn == 2
    assert pack.config.verifier.judge.max_candidate_values == 10
    assert pack.config.verifier.plausibility.date_bound_grace_days == 0
    # Duration pass knobs ride the same block with the same defaults rule.
    assert pack.config.verifier.plausibility.enforce_duration_span_bound is False
    assert pack.config.verifier.plausibility.degenerate_duration_min_basis == 5
    assert pack.config.verifier.plausibility.challenge_degenerate_durations is True


def test_verifier_settings_typo_rejected(make_pack):
    config = copy.deepcopy(VALID_CONFIG)
    config["verifier"] = {"plausibility": {"row_count_tolerence_pct": 5}}  # typo
    with pytest.raises(PackLoadError, match="row_count_tolerence_pct"):
        load_pack(make_pack(config))


def test_pull_block_loads_and_typos_fail(make_pack):
    """`engine pull` reads its table list from the pack; `schema` is the
    yaml key (the attribute is schema_name); unknown keys are typos."""
    config = copy.deepcopy(VALID_CONFIG)
    config["pull"] = {
        "warehouse_id": "abc123",
        "catalog": "main",
        "schema": "app",
        "tables": [
            {"name": "invoices", "key": "id"},
            {"name": "v_suppliers", "versioned": False, "where": "active = true"},
        ],
    }
    pack = load_pack(make_pack(config))
    pull = pack.config.pull
    assert (pull.warehouse_id, pull.catalog, pull.schema_name) == ("abc123", "main", "app")
    assert pull.page_rows == 20000
    assert [(t.name, t.key, t.versioned, t.where) for t in pull.tables] == [
        ("invoices", "id", True, None),
        ("v_suppliers", None, False, "active = true"),
    ]

    config["pull"]["tables"][0]["primary_key"] = "id"
    with pytest.raises(PackLoadError, match="primary_key"):
        load_pack(make_pack(config, name="pack2"))


def test_generation_without_a_simulation_source_loads(make_pack):
    """A real application has no SQLite export and no simulation seed:
    the generation block still carries what the generators need."""
    config = copy.deepcopy(VALID_CONFIG)
    config["generation"] = {
        "component_id_prefix": "app",
        "source_globs": ["src/**/*.py"],
    }
    pack = load_pack(make_pack(config))
    assert pack.config.generation.source_sqlite is None
    assert pack.config.generation.simulation_seed is None
    assert pack.config.generation.component_id_prefix == "app"
