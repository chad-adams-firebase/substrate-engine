"""The eval runner: header + one appended record per (row, rep),
multi-turn conversation continuity, append-safe resume, and errors
that mark a rep without sinking the sweep. All offline: the session
is a scripted fake injected through the _build_session seam."""

import json
import subprocess

import pytest
import yaml

from engine.config.models import PortName, ToolName
from engine.eval import runner
from engine.eval.bank import load_bank
from engine.eval.metering import MeteringLLM
from engine.eval.runner import RunnerError, load_report, run_bank
from engine.harness.outcomes import (
    AnswerOutcome,
    MarkdownAnswer,
    TurnResult,
)
from engine.tools.envelope import ToolInvocation, dumps_turn_evidence
from tests.conftest import VALID_CONFIG
from tests.stubs.llm_stub import ScriptedLLM

ROWS = """\
- id: B5
  provenance: scripted
  category: data
  question: "How many invoices received last week had findings?"
  expect: {exit: [0], assertions: [{kind: nonempty}]}
- id: MT1
  provenance: user-sourced
  category: multiturn
  turns:
    - question: "How many invoices arrived per month?"
      expect: {exit: [0], assertions: [{kind: nonempty}]}
    - question: "How many of those had findings?"
      expect: {exit: [0], assertions: [{kind: nonempty}]}
"""


@pytest.fixture()
def bank(tmp_path):
    root = tmp_path / "evalbank"
    (root / "bank").mkdir(parents=True)
    (root / "eval.yaml").write_text(
        "default_runs: 1\npack: ../pack\n", encoding="utf-8"
    )
    (root / "bank" / "rows.yaml").write_text(ROWS, encoding="utf-8")
    return load_bank(root)


@pytest.fixture()
def pack_dir(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "config.yaml").write_text(
        yaml.safe_dump(VALID_CONFIG), encoding="utf-8"
    )
    return pack


PAYLOAD = dumps_turn_evidence(
    [
        ToolInvocation(
            tool=ToolName.APP_PRIMER,
            arguments={},
            status="ok",
            manifest_ids=["m2", "m1"],
        )
    ]
)


def make_result(conversation_id: int, turn: int, text: str) -> TurnResult:
    return TurnResult(
        conversation_id=conversation_id,
        turn=turn,
        outcome=AnswerOutcome(
            body=MarkdownAnswer(text=text), verification="verified"
        ),
        tools_used=["app_primer"],
        evidence_bundle_ref="ref1",
    )


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, int | None]] = []

    def ask(self, question, conversation_id=None):
        self.calls.append((question, conversation_id))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakePorts:
    def get(self, port):
        assert port == PortName.WORK_STORE

        class Store:
            def load_evidence_bundle(self, ref):
                return PAYLOAD

        return Store()


def install_session(monkeypatch, results):
    session = FakeSession(results)

    def fake_build(pack_dir, work_db, listener):
        return session, FakePorts(), MeteringLLM(ScriptedLLM([]))

    monkeypatch.setattr(runner, "_build_session", fake_build)
    return session


def quiet(line: str) -> None:
    pass


def test_report_shape_and_multiturn_continuity(
    monkeypatch, bank, pack_dir, tmp_path, capsys
):
    session = install_session(
        monkeypatch,
        [
            make_result(1, 1, "146 last week."),
            make_result(2, 1, "674, 682, 634."),
            make_result(2, 2, "254 of those."),
        ],
    )
    out = tmp_path / "report.jsonl"
    assert run_bank(bank, pack_dir, out, status=quiet) == 0
    assert capsys.readouterr().out.strip() == str(out)

    header, records = load_report(out)
    assert header.bank_hash == bank.bank_hash
    assert header.model == "openrouter/auto"
    assert header.target_sha == "abc1234"
    assert [(r.row_id, r.rep) for r in records] == [("B5", 1), ("MT1", 1)]

    # Turn 2 of the multi-turn row continued turn 1's conversation.
    assert session.calls[1] == ("How many invoices arrived per month?", None)
    assert session.calls[2] == ("How many of those had findings?", 2)

    b5 = records[0].turns[0]
    assert b5.exit_equiv == 0
    assert b5.outcome.body.text == "146 last week."
    assert b5.evidence_payload == PAYLOAD
    assert b5.substrate_versions == ["m1", "m2"]


def test_resume_runs_only_missing_reps(monkeypatch, bank, pack_dir, tmp_path):
    out = tmp_path / "report.jsonl"
    install_session(
        monkeypatch,
        [make_result(1, 1, "a"), make_result(2, 1, "b"), make_result(2, 2, "c")],
    )
    run_bank(bank, pack_dir, out, runs=1, status=quiet)

    session = install_session(
        monkeypatch,
        [make_result(3, 1, "d"), make_result(4, 1, "e"), make_result(4, 2, "f")],
    )
    run_bank(bank, pack_dir, out, runs=2, resume=True, status=quiet)
    assert len(session.calls) == 3  # only rep 2 of each row

    _, records = load_report(out)
    assert [(r.row_id, r.rep) for r in records] == [
        ("B5", 1), ("MT1", 1), ("B5", 2), ("MT1", 2),
    ]


def test_existing_report_without_resume_refuses(
    monkeypatch, bank, pack_dir, tmp_path
):
    out = tmp_path / "report.jsonl"
    install_session(monkeypatch, [make_result(1, 1, "a")] * 3)
    run_bank(bank, pack_dir, out, status=quiet)
    with pytest.raises(RunnerError, match="pass --resume"):
        run_bank(bank, pack_dir, out, status=quiet)


def test_resume_refuses_a_different_bank(monkeypatch, bank, pack_dir, tmp_path):
    out = tmp_path / "report.jsonl"
    install_session(monkeypatch, [make_result(1, 1, "a")] * 3)
    run_bank(bank, pack_dir, out, status=quiet)

    (bank.root / "bank" / "rows.yaml").write_text(
        ROWS.replace("last week", "this week"), encoding="utf-8"
    )
    changed = load_bank(bank.root)
    with pytest.raises(RunnerError, match="bank_hash"):
        run_bank(changed, pack_dir, out, resume=True, status=quiet)


def test_resume_truncates_a_partial_trailing_line(
    monkeypatch, bank, pack_dir, tmp_path
):
    out = tmp_path / "report.jsonl"
    install_session(
        monkeypatch,
        [make_result(1, 1, "a"), make_result(2, 1, "b"), make_result(2, 2, "c")],
    )
    run_bank(bank, pack_dir, out, rows=["B5"], status=quiet)
    with out.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"run","row_id":"MT1","rep":1,"tur')

    warnings: list[str] = []
    run_bank(
        bank, pack_dir, out, resume=True, status=warnings.append
    )
    assert any("truncating the partial line" in line for line in warnings)
    _, records = load_report(out)
    assert [(r.row_id, r.rep) for r in records] == [("B5", 1), ("MT1", 1)]


def test_ask_error_is_recorded_and_the_sweep_continues(
    monkeypatch, bank, pack_dir, tmp_path
):
    install_session(
        monkeypatch,
        [
            RuntimeError("boom"),
            make_result(2, 1, "fine"),
            make_result(2, 2, "fine"),
        ],
    )
    out = tmp_path / "report.jsonl"
    run_bank(bank, pack_dir, out, status=quiet)

    _, records = load_report(out)
    failed, healthy = records
    assert failed.row_id == "B5"
    assert failed.turns[0].exit_equiv == 1
    assert failed.turns[0].error == "RuntimeError: boom"
    assert failed.turns[0].outcome is None
    assert healthy.row_id == "MT1"
    assert [t.exit_equiv for t in healthy.turns] == [0, 0]


def test_rows_filter_and_header_records_it(
    monkeypatch, bank, pack_dir, tmp_path
):
    session = install_session(monkeypatch, [make_result(1, 1, "a")])
    out = tmp_path / "report.jsonl"
    run_bank(bank, pack_dir, out, rows=["B5"], status=quiet)
    assert len(session.calls) == 1
    header, records = load_report(out)
    assert header.rows_filter == ["B5"]
    assert [r.row_id for r in records] == ["B5"]


def test_report_lines_are_valid_json(monkeypatch, bank, pack_dir, tmp_path):
    install_session(monkeypatch, [make_result(1, 1, "a")] * 3)
    out = tmp_path / "report.jsonl"
    run_bank(bank, pack_dir, out, status=quiet)
    lines = out.read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["header", "run", "run"]


def test_metering_llm_counts_calls_and_usage():
    from engine.ports.types import LLMResponse, Message, TokenUsage

    meter = MeteringLLM(
        ScriptedLLM(
            [
                LLMResponse(
                    content="a",
                    model="m",
                    usage=TokenUsage(prompt_tokens=10, completion_tokens=2),
                ),
                LLMResponse(content="b", model="m"),  # usage unreported
            ]
        )
    )
    message = [Message(role="user", content="x")]
    meter.complete(message)
    meter.complete(message, temperature=0.0)

    stats = meter.stats()
    assert stats.calls == 2
    assert stats.prompt_tokens == 10
    assert stats.completion_tokens == 2
    assert len(stats.latencies_ms) == 2

    meter.reset()
    assert meter.stats().calls == 0


def test_engine_dirty_means_modified_tracked_content(tmp_path):
    """4b findings, provenance note: the runner writes its report into
    the repo, so an untracked-file-counting flag was permanently true
    and carried no information. Untracked files are clean; a modified
    tracked file is dirty."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["config", "user.email", "t@t"], check=True)
    subprocess.run(git + ["config", "user.name", "t"], check=True)
    (repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(git + ["add", "tracked.txt"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "one"], check=True)

    sha, dirty = runner._engine_sha(repo)
    assert len(sha) == 40 and dirty is False

    (repo / "reports").mkdir()
    (repo / "reports" / "run.jsonl").write_text("{}\n", encoding="utf-8")
    assert runner._engine_sha(repo) == (sha, False)  # untracked: clean

    (repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
    assert runner._engine_sha(repo) == (sha, True)  # modified: dirty


def test_nudges_and_lenient_parses_are_counted_from_the_trail(
    monkeypatch, bank, pack_dir, tmp_path
):
    """Polish Pass: the router's channel habit is a number in the
    record — prose nudged back, and text-form verbs read as the call."""
    from datetime import UTC, datetime

    from engine.harness.events import StatusEvent

    now = datetime.now(UTC)
    habit = make_result(1, 1, "146 last week.")
    habit = habit.model_copy(
        update={
            "events": [
                StatusEvent(node="route", phase="finish", detail="protocol violation — nudging", at=now, raw_response="prose"),
                StatusEvent(node="route", phase="finish", detail="protocol violation — nudging", at=now, raw_response="prose"),
                StatusEvent(node="route", phase="finish", detail="text-form give_answer parsed as the call", at=now, raw_response='give_answer({"shape":"prose"})'),
                StatusEvent(node="route", phase="finish", detail="decision: answer", at=now),
            ]
        }
    )
    install_session(
        monkeypatch,
        [habit, make_result(2, 1, "674, 682, 634."), make_result(2, 2, "254 of those.")],
    )
    out = tmp_path / "report.jsonl"
    assert run_bank(bank, pack_dir, out, status=quiet) == 0
    _, records = load_report(out)
    b5 = records[0].turns[0]
    assert (b5.nudges, b5.lenient_parses) == (2, 1)
