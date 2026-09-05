"""CLI wiring: convert/generate/validate compose pack -> container ->
generators, and the SHA pin is enforced against a real git clone."""

import shutil
import subprocess

import pytest
import yaml

from engine.cli import main

from tests.fixture_generation import SNAPSHOT

SHA = "761a18e9b9253870d930f1b13b3a852ce516d603"


@pytest.fixture()
def cli_pack(snapshot_sqlite, tmp_path):
    """A pack directory wired to the snapshot source and DB slice."""
    pack = tmp_path / "pack"
    pack.mkdir()
    source_root = tmp_path / "source"
    shutil.copytree(SNAPSHOT / "source", source_root)
    config = {
        "name": "clipack",
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
                "settings": {"root": str(source_root), "commit_sha": SHA},
            },
        },
        "generation": {
            "source_sqlite": str(snapshot_sqlite),
            "simulation_seed": 42,
            "component_id_prefix": "ig",
            "source_globs": ["src/invoiceguard/**/*.py"],
        },
    }
    (pack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    shutil.copy(SNAPSHOT / "components.yaml", pack / "components.yaml")
    shutil.copy(SNAPSHOT / "primer.md", pack / "primer.md")
    return pack


def test_convert_then_generate_then_validate(cli_pack, capsys):
    assert main(["convert", "--pack", str(cli_pack)]) == 0
    assert (cli_pack / "app.duckdb").is_file()
    assert (cli_pack / "substrates" / "manifests" / "sqlite_convert.json").is_file()

    assert main(["generate", "--pack", str(cli_pack)]) == 0
    for substrate in (
        "dictionary",
        "univariate_stats",
        "ckg_nodes",
        "ckg_edges",
        "ckg_conditionals",
        "component_memberships",
    ):
        assert (cli_pack / "substrates" / f"{substrate}.jsonl").is_file(), substrate
    for manifest in ("dictionary", "stats", "ckg"):
        assert (cli_pack / "substrates" / "manifests" / f"{manifest}.json").is_file()

    assert main(["generate", "--pack", str(cli_pack), "--check"]) == 0
    out = capsys.readouterr().out
    assert "byte-identical" in out

    assert main(["validate", "--pack", str(cli_pack)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_only_subset_generates_only_that(cli_pack):
    assert main(["convert", "--pack", str(cli_pack)]) == 0
    assert main(["generate", "--pack", str(cli_pack), "--only", "stats"]) == 0
    assert (cli_pack / "substrates" / "univariate_stats.jsonl").is_file()
    assert not (cli_pack / "substrates" / "dictionary.jsonl").exists()


def test_convert_sqlite_flag_resolves_from_cwd(
    cli_pack, snapshot_sqlite, tmp_path, monkeypatch
):
    """--sqlite is cwd-relative like any Unix tool's argument; it must
    not be joined onto the pack directory the way config values are."""
    work = tmp_path / "work" / "db"
    work.mkdir(parents=True)
    shutil.copy(snapshot_sqlite, work / "invoiceguard.db")
    monkeypatch.chdir(tmp_path / "work")

    code = main(
        ["convert", "--pack", str(cli_pack), "--sqlite", "db/invoiceguard.db"]
    )
    assert code == 0
    assert (cli_pack / "app.duckdb").is_file()


def test_generate_source_flag_resolves_from_cwd(cli_pack, tmp_path, monkeypatch):
    """--source is cwd-relative too; before the fix the relative value
    was injected into adapter settings and resolved pack-relative."""
    assert main(["convert", "--pack", str(cli_pack)]) == 0
    work = tmp_path / "work"
    shutil.copytree(SNAPSHOT / "source", work / "source")
    monkeypatch.chdir(work)

    code = main(["generate", "--pack", str(cli_pack), "--source", "source"])
    assert code == 0
    assert (cli_pack / "substrates" / "ckg_nodes.jsonl").is_file()


def test_config_sqlite_path_stays_pack_relative(
    cli_pack, snapshot_sqlite, tmp_path, monkeypatch
):
    """config.yaml paths keep resolving against the pack directory no
    matter where the CLI is invoked from — only flags are cwd-relative."""
    data = cli_pack / "data"
    data.mkdir()
    shutil.copy(snapshot_sqlite, data / "invoiceguard.db")
    config = yaml.safe_load((cli_pack / "config.yaml").read_text(encoding="utf-8"))
    config["generation"]["source_sqlite"] = "data/invoiceguard.db"
    (cli_pack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main(["convert", "--pack", str(cli_pack)]) == 0
    assert (cli_pack / "app.duckdb").is_file()


def test_sha_mismatch_refuses(cli_pack, tmp_path, capsys):
    """A source root that is a git clone at a different HEAD than the
    pack's pin must refuse — silent extraction at the wrong commit
    invalidates every line reference."""
    clone = tmp_path / "clone"
    shutil.copytree(SNAPSHOT / "source", clone)
    git = ["git", "-C", str(clone), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)

    assert main(["convert", "--pack", str(cli_pack)]) == 0
    code = main(["generate", "--pack", str(cli_pack), "--source", str(clone)])
    assert code == 1
    assert "pins" in capsys.readouterr().err


def test_convert_without_a_sqlite_source_is_legible(cli_pack, capsys):
    """A pack whose database comes from the warehouse has no
    source_sqlite; asking it to convert must name the fix, not crash
    on a missing path."""
    config_path = cli_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del config["generation"]["source_sqlite"]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert main(["convert", "--pack", str(cli_pack)]) == 1
    err = capsys.readouterr().err
    assert "generation.source_sqlite" in err and "engine pull" in err
