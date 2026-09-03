"""A work store written before Phase 5 Blocks 3 and 4 keeps loading
(Brief §10.3: no migration, no forced reset). The committed fixture
(tests/fixtures/legacy_work_db/README.md) has the 12-column turn_log
and a checkpoint whose history is (user, assistant) Message pairs; a
conversation from it continues, the pairs upgrade to HistoryTurn
records on read, the turn log gains its two columns in place, and the
backfill verb recovers the legacy questions."""

import shutil
import sqlite3
from pathlib import Path

import yaml

from engine.cli import main
from engine.config.models import PortName
from engine.harness.state import HistoryTurn, TurnState, upgrade_history
from engine.ports.types import Message
from tests.harness_support import build_ask_session, tool_call

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_work_db" / "work.db"
THREAD = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}


def _legacy_pack(tool_pack, tmp_path):
    database = tmp_path / "work.db"
    shutil.copy(FIXTURE, database)
    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["adapters"]["work_store"]["settings"]["database"] = str(database)
    config_path.write_text(yaml.safe_dump(config))
    return tool_pack, database


def test_the_fixture_is_the_pre_block_3_layout():
    connection = sqlite3.connect(str(FIXTURE))
    try:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(turn_log)")
        ]
        rows = connection.execute(
            "SELECT conversation_id, turn FROM turn_log ORDER BY turn"
        ).fetchall()
        checkpoints = connection.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id = '1'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert len(columns) == 12
    assert "question" not in columns and "outcome" not in columns
    assert rows == [(1, 1), (1, 2)]
    assert checkpoints > 0


def test_a_legacy_conversation_continues_and_its_history_upgrades(
    tool_pack, tmp_path
):
    pack, _ = _legacy_pack(tool_pack, tmp_path)
    session, ports, _ = build_ask_session(
        pack, [tool_call("refuse", {"reason": "asked and answered"})]
    )
    store = ports.get(PortName.WORK_STORE)

    # Before the turn: the thread's history channel is Message pairs.
    saver = store.checkpointer()
    try:
        before = saver.get(THREAD)["channel_values"]["history"]
    finally:
        saver.conn.close()
    assert [type(m).__name__ for m in before] == ["Message"] * 4
    assert TurnState(history=before).history == [
        HistoryTurn(
            turn=1,
            question="How many invoice rows are there?",
            answer="Invoices has 50 rows.",
            kind="prose",
        ),
        HistoryTurn(
            turn=2,
            question="Show me the status distribution as a table.",
            answer="[table: result set]",
            kind="table",
        ),
    ]

    result = session.ask("what did I ask first?", conversation_id=1)
    assert result.turn == 3 and result.outcome.kind == "refuse"

    # The router saw both legacy turns, expanded exactly as before.
    contents = [m.content for m in ports.get(PortName.LLM).calls[0]["messages"]]
    assert contents[1:5] == [
        "How many invoice rows are there?",
        "Invoices has 50 rows.",
        "Show me the status distribution as a table.",
        "[table: result set]",
    ]

    # After the turn: the channel holds records, numbered 1..3.
    saver = store.checkpointer()
    try:
        after = saver.get(THREAD)["channel_values"]["history"]
    finally:
        saver.conn.close()
    assert [type(r).__name__ for r in after] == ["HistoryTurn"] * 3
    assert [(r.turn, r.kind) for r in after] == [
        (1, "prose"), (2, "table"), (3, "refuse")
    ]

    # The turn log migrated in place: the legacy rows read back with an
    # empty question and no outcome, the new row with both.
    entries = store.list_turn_logs(1)
    assert [e.turn for e in entries] == [1, 2, 3]
    assert entries[0].question == "" and entries[0].outcome is None
    assert entries[2].question == "what did I ask first?"
    assert entries[2].outcome is not None
    assert entries[0].substrate_versions  # provenance from before survives


def test_backfill_recovers_the_legacy_questions(tool_pack, tmp_path, capsys):
    pack, _ = _legacy_pack(tool_pack, tmp_path)
    capsys.readouterr()
    assert main(["store", "backfill-questions", "--pack", str(pack)]) == 0
    out = capsys.readouterr().out
    assert "conversation 1 turn 1: recovered 'How many invoice rows are there?'" in out
    assert (
        "conversation 1 turn 2: recovered 'Show me the status distribution as a table.'"
        in out
    )
    assert "recovered 2 question(s); 0 without history." in out
    _, ports, _ = build_ask_session(pack, [])
    store = ports.get(PortName.WORK_STORE)
    assert [e.question for e in store.list_turn_logs(1)] == [
        "How many invoice rows are there?",
        "Show me the status distribution as a table.",
    ]
    assert store.turns_without_question() == []


def test_upgrade_history_reads_pairs_dicts_and_records():
    pairs = [
        Message(role="user", content="q1"),
        Message(role="assistant", content="a1"),
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "[refused: no]"},
        {"role": "user", "content": "q3 without its answer"},
    ]
    assert upgrade_history(pairs) == [
        HistoryTurn(turn=1, question="q1", answer="a1", kind="prose"),
        HistoryTurn(turn=2, question="q2", answer="[refused: no]", kind="refuse"),
    ]
    record = HistoryTurn(turn=7, question="q", answer="[clarify: which?]", kind="clarify")
    assert upgrade_history([record]) == [record]
    as_dict = {"turn": 4, "question": "q", "answer": "[escalated: x]", "kind": "escalate"}
    assert upgrade_history([as_dict]) == [as_dict]
    assert TurnState(history=[as_dict]).history == [HistoryTurn(**as_dict)]
    assert upgrade_history("not a list") == "not a list"
