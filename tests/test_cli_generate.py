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
