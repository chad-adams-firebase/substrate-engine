"""Smoke CLI: `engine info` against the checked-in example pack, plus
failure behavior on a bad pack."""

from pathlib import Path

from engine.cli import main

EXAMPLE_PACK = Path(__file__).parent.parent / "packs" / "example"


def test_info_reports_example_pack(capsys):
    exit_code = main(["info", "--pack", str(EXAMPLE_PACK)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Pack: example" in out
    assert "data_dictionary" in out
    assert "run_sql" in out
    assert "OpenRouterLLM" in out
    assert "DuckDbSql" in out
    assert "SqliteWorkStore" in out
    assert "FakeUserIdentity" in out
    assert "LocalDirectorySource" in out
    assert "not configured" in out  # substrate_store / execution_log


def test_info_does_not_leave_files_behind():
    """Resolving adapters for a report must not create databases in the
    pack directory."""
    before = set(EXAMPLE_PACK.iterdir())

    main(["info", "--pack", str(EXAMPLE_PACK)])

    assert set(EXAMPLE_PACK.iterdir()) == before


def test_info_on_missing_pack_fails_legibly(tmp_path, capsys):
    exit_code = main(["info", "--pack", str(tmp_path / "nope")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error:" in captured.err
    assert "does not exist" in captured.err
    assert captured.out == ""


def test_info_on_invalid_config_fails_legibly(tmp_path, capsys):
    pack_dir = tmp_path / "badpack"
    pack_dir.mkdir()
    (pack_dir / "config.yaml").write_text("name: broken\n", encoding="utf-8")

    exit_code = main(["info", "--pack", str(pack_dir)])

    assert exit_code == 1
    assert "failed validation" in capsys.readouterr().err
