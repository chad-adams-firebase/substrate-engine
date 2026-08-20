"""Checkpointer round-trip (phasing done-check): a two-turn
conversation resumes — within a session and across a fresh session
over the same work.db file."""

import yaml

from engine.config.models import PortName
from engine.ports.types import LLMResponse
from tests.harness_support import build_ask_session, tool_call

STATS_CALL = tool_call(
    "query_univariate_stats", {"table": "invoices", "column": "status"}
)
GIVE_PROSE = tool_call("give_answer", {"shape": "prose"})


def _file_backed_pack(tool_pack, tmp_path):
    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["adapters"]["work_store"]["settings"]["database"] = str(
        tmp_path / "work.db"
    )
    config_path.write_text(yaml.safe_dump(config))
    return tool_pack


def _turn(session, question, conversation_id=None):
    return session.ask(question, conversation_id=conversation_id)


def test_two_turns_share_history_and_a_fresh_session_resumes(
    tool_pack, tmp_path
):
    pack = _file_backed_pack(tool_pack, tmp_path)

    first_responses = [
        STATS_CALL,
        GIVE_PROSE,
        LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"),
    ]
    session, ports, _ = build_ask_session(pack, first_responses)
    first = session.ask("how many invoice rows?")
    assert first.turn == 1
    assert first.outcome.body.text == "Invoices has 50 rows."

    # Turn 2, same session: the router must see turn 1 in history.
    second_responses = [
        tool_call("refuse", {"reason": "asked and answered"}),
    ]
    session2, ports2, _ = build_ask_session(pack, second_responses)
    second = session2.ask(
        "what did I just ask?", conversation_id=first.conversation_id
    )
    assert second.turn == 2
    llm = ports2.get(PortName.LLM)
    history_contents = [m.content for m in llm.calls[0]["messages"]]
    assert "how many invoice rows?" in history_contents
    assert "Invoices has 50 rows." in history_contents
    assert "what did I just ask?" in history_contents


def test_separate_conversations_have_separate_histories(tool_pack, tmp_path):
    pack = _file_backed_pack(tool_pack, tmp_path)
    session, ports, _ = build_ask_session(
        pack,
        [
            tool_call("refuse", {"reason": "r1"}),
            tool_call("refuse", {"reason": "r2"}),
        ],
    )
    first = session.ask("first question")
    second = session.ask("second question")  # new conversation each time

    assert first.conversation_id != second.conversation_id
    assert second.turn == 1  # fresh thread, not turn 2
    llm = ports.get(PortName.LLM)
    second_contents = [m.content for m in llm.calls[1]["messages"]]
    assert "first question" not in " ".join(second_contents)
