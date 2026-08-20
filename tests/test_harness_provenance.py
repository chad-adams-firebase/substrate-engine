"""Provenance-row completeness (phasing done-check): every §12 field
populated on an answer turn; refuse turns log honestly-null fields;
the evidence ref recomputes from the stored payload."""

import hashlib
import json

from engine.config.models import PortName
from engine.ports.types import LLMResponse
from engine.tools.envelope import loads_turn_evidence
from engine.verifier.models import VerifierVerdict
from tests.harness_support import build_ask_session, tool_call


def test_answer_turn_writes_a_complete_section_12_row(tool_pack):
    responses = [
        tool_call(
            "query_univariate_stats", {"table": "invoices", "column": "status"}
        ),
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(
            content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"
        ),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    result = session.ask("how many rows?")

    store = ports.get(PortName.WORK_STORE)
    (entry,) = store.list_turn_logs(result.conversation_id)

    assert entry.turn == 1
    assert entry.actor == "tester"  # the fake-identity user
    assert entry.action == "ask"
    assert entry.tools_used == ["query_univariate_stats"]
    assert "univariate_statistics" in entry.substrates_read
    assert entry.substrate_versions  # manifest ids of machine rows

    # The bundle is stored, content-addressed, and round-trips.
    payload = store.load_evidence_bundle(entry.evidence_bundle_ref)
    assert payload is not None
    assert (
        hashlib.sha256(payload.encode()).hexdigest()[:16]
        == entry.evidence_bundle_ref
    )
    evidence = loads_turn_evidence(payload)
    assert evidence[0].tool.value == "query_univariate_stats"

    # Verdict JSON parses back into the model, claim detail included.
    verdict = VerifierVerdict.model_validate_json(entry.verifier_verdict)
    assert verdict.disposition == "verified"

    # Status events persisted with start+finish per emitting node.
    events = json.loads(entry.status_events)
    phases = {(e["node"], e["phase"]) for e in events}
    assert ("route", "start") in phases and ("route", "finish") in phases
    assert ("verify", "start") in phases and ("verify", "finish") in phases
    assert all(e["at"] for e in events)


def test_refuse_turn_logs_null_verdict_and_bundle(tool_pack):
    session, ports, _ = build_ask_session(
        tool_pack, [tool_call("refuse", {"reason": "out of scope"})]
    )
    result = session.ask("please deploy to prod")

    store = ports.get(PortName.WORK_STORE)
    (entry,) = store.list_turn_logs(result.conversation_id)
    assert entry.tools_used == []
    assert entry.evidence_bundle_ref is None
    assert entry.verifier_verdict is None
    assert entry.substrate_versions == []
    assert json.loads(entry.status_events)  # the trail still persists


def test_conversation_lands_in_the_scratch_workspace(tool_pack):
    session, ports, _ = build_ask_session(
        tool_pack, [tool_call("refuse", {"reason": "r"})]
    )
    result = session.ask("q")
    store = ports.get(PortName.WORK_STORE)
    (workspace,) = store.list_workspaces("tester")
    assert workspace.name == "scratch"
    conversation = store.get_conversation(result.conversation_id)
    assert conversation.workspace_id == workspace.id
    assert conversation.title == "q"
