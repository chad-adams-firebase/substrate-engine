"""The runbook's config template loads as printed: a pack author copies
it, so a field the schema renamed or a tool the surface dropped fails
here, not on the work machine."""

import re
from pathlib import Path

import yaml

from engine.config.models import PackConfig, PortName, SubstrateName, ToolName

RUNBOOK = Path(__file__).parent.parent / "docs" / "pack-authoring-runbook.md"


def _template() -> dict:
    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    block = next(b for b in blocks if b.startswith("name: local-<name>"))
    return yaml.safe_load(block)


def test_the_real_pack_template_loads_and_drops_the_log_tool():
    config = PackConfig.model_validate(_template())
    assert config.adapters[PortName.LLM].adapter == "databricks_fm"
    assert PortName.EXECUTION_LOG not in config.adapters
    assert ToolName.CHECK_EXECUTION not in config.tools
    assert SubstrateName.APPLICATION_LOGS not in config.substrates
    assert config.pull is not None and config.pull.tables[0].key
    assert config.generation is not None
    assert config.generation.source_sqlite is None
    assert config.generation.simulation_seed is None
