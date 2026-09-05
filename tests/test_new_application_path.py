"""The work-side path, offline: a pack whose world is pulled from a
warehouse (the fake serving the vendored snapshot's rows in the API's
own JSON), whose generation block has no SQLite export and no seed,
and whose source is a local directory pinned by a git-init'd commit —
pull, generate, validate, info through the CLI exactly as the runbook
says. The Mac's stand-in for the real demo path."""

import shutil
import sqlite3
import subprocess

import pytest
import yaml

from engine.cli import main
from engine.packtools import pull_databricks
from engine.substrates.manifest import load_manifest
from tests.fake_warehouse import FakeWarehouse
from tests.fixture_generation import SNAPSHOT


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def pulled_pack(snapshot_sqlite, tmp_path, monkeypatch):
    # The target application's source as a local directory: git init +
    # one commit mints the pin (the runbook's step for a work-side copy).
    source = tmp_path / "target-app"
    shutil.copytree(SNAPSHOT / "source", source)
    _git("init", "-q", cwd=source)
    _git("config", "user.email", "t@t", cwd=source)
    _git("config", "user.name", "t", cwd=source)
    _git("add", "-A", cwd=source)
    _git("commit", "-q", "-m", "pin", cwd=source)
    sha = _git("rev-parse", "HEAD", cwd=source)

    fake = FakeWarehouse.from_sqlite(snapshot_sqlite, catalog="main", schema="app", chunk_rows=37)
    monkeypatch.setattr(pull_databricks, "_client_factory", fake.client_factory)
    monkeypatch.setattr(pull_databricks, "POLL_SECONDS", 0)
    monkeypatch.setenv(pull_databricks.HOST_ENV_VAR, "workspace.test")
    monkeypatch.setenv(pull_databricks.TOKEN_ENV_VAR, "dapi-test")

    pack = tmp_path / "local-app"
    pack.mkdir()
    config = {
        "name": "local-app",
        "substrates": ["data_dictionary", "application_database"],
        "tools": ["run_sql"],
        "adapters": {
            "sql": {"adapter": "duckdb", "settings": {"database": "app.duckdb"}},
            "identity": {
                "adapter": "fake_user",
                "settings": {"username": "t", "display_name": "T"},
            },
            "source_code": {
                "adapter": "local_directory",
                "settings": {"root": str(source), "commit_sha": sha},
            },
        },
        "generation": {
            "component_id_prefix": "ig",
            "source_globs": ["src/invoiceguard/**/*.py"],
        },
        "pull": {
            "warehouse_id": "wh-1",
            "catalog": "main",
            "schema": "app",
            "page_rows": 50,
            "tables": [
                {"name": name, **({"key": key} if key else {})}
                for name, key in sorted(fake.keys.items())
            ],
        },
    }
    (pack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    shutil.copy(SNAPSHOT / "components.yaml", pack / "components.yaml")
    shutil.copy(SNAPSHOT / "primer.md", pack / "primer.md")
    return pack, fake, sha


def test_pull_generate_validate_info(pulled_pack, snapshot_sqlite, capsys):
    pack, fake, sha = pulled_pack

    assert main(["pull", "--pack", str(pack), "--dry-run"]) == 0
    planned = capsys.readouterr().out
    assert "DESCRIBE HISTORY `main`.`app`.`invoices` LIMIT 1" in planned
    assert fake.statements == []  # nothing left the machine

    assert main(["pull", "--pack", str(pack)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("pulled main.app -> ")
    assert "  invoices " in out and "manifest " in out
    manifest = load_manifest(pack / "substrates" / "manifests" / "databricks_pull.json")
    assert manifest.generator == "databricks_pull"
    assert manifest.source_commit_sha is None
    assert manifest.source_snapshot.startswith("main.app|")
    connection = sqlite3.connect(str(snapshot_sqlite))
    expected_tables = sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    connection.close()
    assert manifest.source_tables == expected_tables

    assert main(["generate", "--pack", str(pack)]) == 0
    capsys.readouterr()
    for name in ("dictionary", "stats", "ckg"):
        assert (pack / "substrates" / "manifests" / f"{name}.json").is_file()
    assert load_manifest(pack / "substrates" / "manifests" / "ckg.json").source_commit_sha == sha
    dictionary = (pack / "substrates" / "dictionary.jsonl").read_text(encoding="utf-8")
    assert '"table_name":"invoices"' in dictionary

    assert main(["validate", "--pack", str(pack)]) == 0
    report = capsys.readouterr().out
    assert "RESULT: PASS" in report

    assert main(["info", "--pack", str(pack)]) == 0
    assert "DuckDbSql" in capsys.readouterr().out


def test_missing_credentials_name_the_variable(pulled_pack, monkeypatch, capsys):
    pack, _, _ = pulled_pack
    monkeypatch.delenv(pull_databricks.TOKEN_ENV_VAR)
    assert main(["pull", "--pack", str(pack)]) == 1
    assert pull_databricks.TOKEN_ENV_VAR in capsys.readouterr().err


def test_a_pack_without_a_pull_block_is_told_so(pulled_pack, capsys):
    pack, _, _ = pulled_pack
    config = yaml.safe_load((pack / "config.yaml").read_text(encoding="utf-8"))
    del config["pull"]
    (pack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    assert main(["pull", "--pack", str(pack)]) == 1
    assert "'pull:' section" in capsys.readouterr().err
