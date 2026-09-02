"""engine ask / engine turns: exit codes, stream separation, and the
provenance inspection path."""

import json

import yaml

from engine.cli import main
from engine.harness.events import StatusEvent
from engine.harness.outcomes import (
    AnswerOutcome,
    ClarifyOutcome,
    EscalateOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
    TurnResult,
)
from engine.ports.types import LLMResponse
from engine.tools.envelope import Table
from tests.harness_support import build_ask_session, tool_call


class _StubSession:
    def __init__(self, result: TurnResult) -> None:
        self._result = result
        self.asked: list[tuple[str, int | None]] = []

    def ask(self, question, conversation_id=None):
        self.asked.append((question, conversation_id))
        return self._result


def _result(outcome) -> TurnResult:
    return TurnResult(conversation_id=7, turn=3, outcome=outcome)


def _patch(monkeypatch, outcome):
    session = _StubSession(_result(outcome))
    monkeypatch.setattr(
        "engine.cli._build_session", lambda pack, listener: (session, None)
    )
    return session


def test_exit_codes_and_stream_separation(monkeypatch, capsys, tool_pack):
    cases = [
        (
            AnswerOutcome(
                body=MarkdownAnswer(text="146 of 161."), verification="verified"
            ),
            0,
            "146 of 161.",
        ),
        (
            AnswerOutcome(
                body=MarkdownAnswer(text="146-ish."), verification="unverified"
            ),
            2,
            "[UNVERIFIED]",
        ),
        (RefuseOutcome(reason="out of scope", what_would_work="data", detail="budget: 8 steps"), 3, "REFUSED"),
        (ClarifyOutcome(question="which week?"), 4, "CLARIFY"),
        (EscalateOutcome(reason="policy"), 5, "ESCALATED"),
    ]
    for outcome, code, needle in cases:
        _patch(monkeypatch, outcome)
        assert main(["ask", "--pack", str(tool_pack), "q"]) == code
        captured = capsys.readouterr()
        assert needle in captured.out
        if needle == "REFUSED":
            assert "Detail: budget: 8 steps" in captured.out  # the engineer's surface
        assert "conversation 7 · turn 3" in captured.err
        assert "conversation" not in captured.out  # stdout stays pure


def test_table_answers_render_aligned_with_caption(monkeypatch, capsys, tool_pack):
    outcome = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["status", "n"],
                rows=[{"status": "flagged", "n": 146}, {"status": "clean", "n": 15}],
                total_row_count=2,
            ),
            caption="SELECT status, COUNT(*) AS n FROM invoices GROUP BY 1",
        ),
        verification="verified",
    )
    _patch(monkeypatch, outcome)
    assert main(["ask", "--pack", str(tool_pack), "q"]) == 0
    out = capsys.readouterr().out
    assert "status" in out and "flagged" in out
    assert "SELECT status" in out


def test_json_flag_dumps_the_turn_result(monkeypatch, capsys, tool_pack):
    _patch(
        monkeypatch,
        AnswerOutcome(body=MarkdownAnswer(text="hi"), verification="verified"),
    )
    assert main(["ask", "--pack", str(tool_pack), "q", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["conversation_id"] == 7
    assert payload["outcome"]["kind"] == "answer"


def test_conversation_flag_reaches_the_session(monkeypatch, capsys, tool_pack):
    session = _patch(
        monkeypatch,
        AnswerOutcome(body=MarkdownAnswer(text="hi"), verification="verified"),
    )
    main(["ask", "--pack", str(tool_pack), "q", "--conversation", "7"])
    assert session.asked == [("q", 7)]


def test_status_listener_prints_a_stderr_trail(monkeypatch, capsys, tool_pack):
    def fake_build(pack, listener):
        listener(
            StatusEvent(
                node="route",
                phase="start",
                detail="Consulting router (step 1)…",
                at="2026-05-30T00:00:00+00:00",
            )
        )
        return (
            _StubSession(
                _result(
                    AnswerOutcome(
                        body=MarkdownAnswer(text="x"), verification="verified"
                    )
                )
            ),
            None,
        )

    monkeypatch.setattr("engine.cli._build_session", fake_build)
    main(["ask", "--pack", str(tool_pack), "q"])
    captured = capsys.readouterr()
    assert "Consulting router" in captured.err
    assert "Consulting router" not in captured.out


def test_turns_lists_conversations_rows_and_evidence(tool_pack, tmp_path, capsys):
    # A real turn through the graph against a file-backed pack, then
    # inspected through the CLI — the documented provenance path.
    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["adapters"]["work_store"]["settings"]["database"] = str(
        tmp_path / "work.db"
    )
    config_path.write_text(yaml.safe_dump(config))

    responses = [
        tool_call(
            "query_univariate_stats", {"table": "invoices", "column": "status"}
        ),
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"),
    ]
    session, _, _ = build_ask_session(tool_pack, responses)
    result = session.ask("how many rows?")
    capsys.readouterr()

    assert main(["turns", "--pack", str(tool_pack)]) == 0
    listing = capsys.readouterr().out
    assert "how many rows?" in listing and "1 turn(s)" in listing

    conv = str(result.conversation_id)
    assert main(["turns", "--pack", str(tool_pack), "--conversation", conv]) == 0
    rows = capsys.readouterr().out
    assert "verdict=verified" in rows
    assert "tools=query_univariate_stats" in rows

    assert (
        main(
            [
                "turns",
                "--pack",
                str(tool_pack),
                "--conversation",
                conv,
                "--turn",
                "1",
                "--evidence",
            ]
        )
        == 0
    )
    detail = capsys.readouterr().out
    assert '"actor": "tester"' in detail
    assert "--- evidence" in detail
    assert "query_univariate_stats" in detail

    assert (
        main(["turns", "--pack", str(tool_pack), "--conversation", "999"]) == 1
    )
    assert "No turns logged" in capsys.readouterr().err


def test_table_answers_render_money_hints_as_currency(monkeypatch, capsys, tool_pack):
    from engine.tools.envelope import ColumnFormat

    outcome = AnswerOutcome(
        body=TableAnswer(
            table=Table(
                columns=["backlog_count", "total_opportunity"],
                rows=[{"backlog_count": 78, "total_opportunity": 8308.92139244107}],
                total_row_count=1,
                column_formats={
                    "total_opportunity": ColumnFormat(kind="money", symbol="$")
                },
            ),
        ),
        verification="verified",
    )
    _patch(monkeypatch, outcome)
    assert main(["ask", "--pack", str(tool_pack), "q"]) == 0
    out = capsys.readouterr().out
    assert "$8,308.92" in out
    assert "8308.92139" not in out
