from pathlib import Path

import pytest
import yaml

# A complete, valid pack config used as the baseline by loader and
# container tests; individual tests override pieces to probe failures.
VALID_CONFIG: dict = {
    "name": "testpack",
    "description": "Pack used by unit tests.",
    "substrates": ["data_dictionary", "application_database"],
    "tools": ["lookup_data_dictionary", "run_sql"],
    "adapters": {
        "llm": {
            "adapter": "openrouter",
            "settings": {"model": "openrouter/auto"},
        },
        "sql": {
            "adapter": "duckdb",
            "settings": {"database": ":memory:"},
        },
        "work_store": {
            "adapter": "sqlite",
            "settings": {"database": ":memory:"},
        },
        "identity": {
            "adapter": "fake_user",
            "settings": {"username": "tester", "display_name": "Test User"},
        },
        "source_code": {
            "adapter": "local_directory",
            "settings": {"root": ".", "commit_sha": "abc1234"},
        },
    },
}


@pytest.fixture
def make_pack(tmp_path: Path):
    """Write a pack directory from a config dict and return its path."""

    def _make(config: dict, name: str = "pack") -> Path:
        pack_dir = tmp_path / name
        pack_dir.mkdir()
        (pack_dir / "config.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8"
        )
        return pack_dir

    return _make
