"""CLI wiring for `engine eval run|grade`: exit-code propagation,
stdout/stderr split, and the offline check-gold path."""

import yaml

from engine import cli
from engine.eval import runner
from tests.conftest import VALID_CONFIG
from tests.test_eval_runner import FakePorts, FakeSession, make_result
from engine.eval.metering import MeteringLLM
from tests.stubs.llm_stub import ScriptedLLM

ROWS = """\
- id: B5
  provenance: scripted
  category: data
  question: "How many last week?"
  expect: {exit: [0], assertions: [{kind: nonempty}]}
"""


def write_env(tmp_path, rows=ROWS, gold=None, expected_gold=None):
    root = tmp_path / "evalbank"
    (root / "bank").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "eval.yaml").write_text(
        "default_runs: 1\npack: ../pack\n", encoding="utf-8"
    )
    body = rows
    if gold is not None:
        (root / "gold" / "g.py").write_text(gold, encoding="utf-8")
        body = rows.replace(
            "  expect:",
            f"  gold: gold/g.py\n  expected_gold: {expected_gold}\n  expect:",
        )
    (root / "bank" / "rows.yaml").write_text(body, encoding="utf-8")

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.yaml").write_text(
        yaml.safe_dump(VALID_CONFIG), encoding="utf-8"
    )
    return root, pack


def install_session(monkeypatch, results):
    session = FakeSession(results)
    monkeypatch.setattr(
        runner,
        "_build_session",
        lambda pack_dir, work_db, listener: (
            session, FakePorts(), MeteringLLM(ScriptedLLM([])),
        ),
    )
    return session


def test_eval_run_then_grade_roundtrip(monkeypatch, tmp_path, capsys):
    bank, pack = write_env(tmp_path)
    install_session(monkeypatch, [make_result(1, 1, "146 last week.")])
    out = tmp_path / "report.jsonl"

    assert cli.main(["eval", "run", "--bank", str(bank), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(out)
    assert "[B5 rep 1/1] exit 0" in captured.err

    grade_out = tmp_path / "grade.txt"
    code = cli.main(
        ["eval", "grade", "--bank", str(bank), "--report", str(out),
         "--pack", str(pack), "--out", str(grade_out)]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "RESULT: PASS" in captured.out
    assert grade_out.read_text(encoding="utf-8") == captured.out


def test_eval_grade_propagates_threshold_failures(
    monkeypatch, tmp_path, capsys
):
    bank, pack = write_env(tmp_path)
    install_session(
        monkeypatch, [make_result(1, 1, "")]  # empty answer: nonempty fails
    )
    out = tmp_path / "report.jsonl"
    cli.main(["eval", "run", "--bank", str(bank), "--out", str(out)])
    capsys.readouterr()

    code = cli.main(
        ["eval", "grade", "--bank", str(bank), "--report", str(out),
         "--pack", str(pack)]
    )
    assert code == 2
    assert "RESULT: FAIL (thresholds)" in capsys.readouterr().out


def test_bad_bank_is_a_cli_error(tmp_path, capsys):
    assert (
        cli.main(
            ["eval", "run", "--bank", str(tmp_path / "absent"),
             "--out", str(tmp_path / "r.jsonl")]
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err


def test_check_gold_reports_rot(tmp_path, capsys):
    bank, pack = write_env(
        tmp_path,
        gold="def gold(world):\n    return {'value': 1}\n",
        expected_gold="{value: 2}",
    )
    code = cli.main(
        ["eval", "grade", "--bank", str(bank), "--pack", str(pack),
         "--check-gold"]
    )
    assert code == 3
    assert "ROT" in capsys.readouterr().out


def test_check_gold_passes_when_healthy(tmp_path, capsys):
    bank, pack = write_env(
        tmp_path,
        gold="def gold(world):\n    return {'value': 1}\n",
        expected_gold="{value: 1}",
    )
    code = cli.main(
        ["eval", "grade", "--bank", str(bank), "--pack", str(pack),
         "--check-gold"]
    )
    assert code == 0
    assert "RESULT: PASS" in capsys.readouterr().out
