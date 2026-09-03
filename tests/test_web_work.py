"""The Block 3 routes: workspaces and conversations CRUD, owner
scoping, the turns endpoint (JSON and text) after a real turn through
the graph, and the evidence endpoint. Flask test client only."""

import json

from engine.config.models import PortName, UiSettings
from engine.harness.outcomes import RefuseOutcome, TurnResult
from engine.ports.types import LLMResponse, User
from engine.web.app import create_app
from tests.harness_support import build_ask_session, tool_call
from tests.test_web_ask import _StubSession, parse_frames


class _Identity:
    def __init__(self, username="tester"):
        self._username = username

    def current_user(self):
        return User(username=self._username, display_name="Test User")


def _real_app(tool_pack, responses):
    session, ports, verifier = build_ask_session(tool_pack, responses)
    app = create_app(
        session,
        ports.get(PortName.WORK_STORE),
        ports.get(PortName.IDENTITY),
        ui=UiSettings(),
        pack_name="toolpack",
    )
    return app, ports, verifier


def _stub_app(tool_pack, *, busy=False, username="tester"):
    """A real store with a stub session — CRUD without a graph."""
    _, ports, _ = build_ask_session(tool_pack, [])
    session = _StubSession(TurnResult(conversation_id=1, turn=1, outcome=RefuseOutcome(reason="r")), busy=busy)
    store = ports.get(PortName.WORK_STORE)
    store.ensure_schema()
    return create_app(session, store, _Identity(username), ui=UiSettings(), pack_name="p"), store


def test_first_listing_creates_the_scratch_workspace(tool_pack):
    app, store = _stub_app(tool_pack)
    client = app.test_client()
    listed = client.get("/api/workspaces").get_json()
    assert [w["name"] for w in listed] == ["scratch"]
    assert listed[0]["owner"] == "tester" and isinstance(listed[0]["id"], int)
    assert client.get("/api/workspaces").get_json() == listed  # idempotent
    assert [w.name for w in store.list_workspaces("tester")] == ["scratch"]


def test_workspace_create_and_delete_only_when_empty(tool_pack):
    app, _ = _stub_app(tool_pack)
    client = app.test_client()
    created = client.post("/api/workspaces", json={"name": "audit"})
    assert created.status_code == 201
    workspace = created.get_json()
    assert workspace["name"] == "audit"
    assert client.post("/api/workspaces", json={"name": "  "}).status_code == 400
    assert client.post("/api/workspaces", json={}).status_code == 400

    conversation = client.post(
        f"/api/workspaces/{workspace['id']}/conversations", json={"title": "t"}
    ).get_json()
    blocked = client.delete(f"/api/workspaces/{workspace['id']}")
    assert blocked.status_code == 409 and "1 conversation" in blocked.get_json()["message"]

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    assert client.delete(f"/api/workspaces/{workspace['id']}").status_code == 204
    assert client.delete(f"/api/workspaces/{workspace['id']}").status_code == 404
    names = [w["name"] for w in client.get("/api/workspaces").get_json()]
    assert "audit" not in names


def test_conversation_crud_and_isolation_between_workspaces(tool_pack):
    app, _ = _stub_app(tool_pack)
    client = app.test_client()
    [scratch] = client.get("/api/workspaces").get_json()
    audit = client.post("/api/workspaces", json={"name": "audit"}).get_json()

    first = client.post(
        f"/api/workspaces/{scratch['id']}/conversations", json={"title": "first"}
    )
    assert first.status_code == 201
    first = first.get_json()
    second = client.post(
        f"/api/workspaces/{audit['id']}/conversations", json={"title": "second"}
    ).get_json()

    assert [c["title"] for c in client.get(f"/api/workspaces/{scratch['id']}/conversations").get_json()] == ["first"]
    assert [c["title"] for c in client.get(f"/api/workspaces/{audit['id']}/conversations").get_json()] == ["second"]

    renamed = client.patch(f"/api/conversations/{first['id']}", json={"title": "Flag rates"})
    assert renamed.status_code == 200 and renamed.get_json()["title"] == "Flag rates"
    assert client.patch(f"/api/conversations/{first['id']}", json={"title": ""}).status_code == 400
    assert client.patch("/api/conversations/999", json={"title": "x"}).status_code == 404
    assert client.post("/api/workspaces/999/conversations", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/workspaces/{audit['id']}/conversations", json={}).status_code == 400

    assert client.delete(f"/api/conversations/{second['id']}").status_code == 204
    assert client.delete(f"/api/conversations/{second['id']}").status_code == 404
    assert client.get(f"/api/workspaces/{audit['id']}/conversations").get_json() == []
    assert client.get("/api/conversations/999/turns").status_code == 404


def test_another_users_workspace_is_404_not_403(tool_pack):
    app, store = _stub_app(tool_pack)
    theirs = store.create_workspace("someone-else", "private")
    conversation = store.create_conversation(theirs.id, "secret")
    client = app.test_client()
    assert [w["owner"] for w in client.get("/api/workspaces").get_json()] == ["tester"]
    assert client.get(f"/api/workspaces/{theirs.id}/conversations").status_code == 404
    assert client.delete(f"/api/workspaces/{theirs.id}").status_code == 404
    assert client.get(f"/api/conversations/{conversation.id}/turns").status_code == 404
    assert client.patch(f"/api/conversations/{conversation.id}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/conversations/{conversation.id}").status_code == 404
    assert store.get_conversation(conversation.id) is not None


def test_deleting_a_conversation_under_a_running_turn_is_409(tool_pack):
    app, store = _stub_app(tool_pack, busy=True)
    workspace = store.create_workspace("tester", "scratch")
    conversation = store.create_conversation(workspace.id, "t")
    response = app.test_client().delete(f"/api/conversations/{conversation.id}")
    assert response.status_code == 409
    assert store.get_conversation(conversation.id) is not None


def test_turns_endpoint_returns_what_a_real_turn_logged(tool_pack):
    """A turn through the graph into a chosen workspace, then the
    JSON and text forms of its turns and its evidence bundle."""
    responses = [
        tool_call("query_univariate_stats", {"table": "invoices", "column": "status"}),
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"),
    ]
    app, ports, _ = _real_app(tool_pack, responses)
    client = app.test_client()
    audit = client.post("/api/workspaces", json={"name": "audit"}).get_json()

    frames = parse_frames(
        client.post(
            "/api/ask", json={"question": "how many rows?", "workspace_id": audit["id"]}
        ).get_data(as_text=True)
    )
    result = frames[-1][1]["result"]
    conversation_id = result["conversation_id"]
    listed = client.get(f"/api/workspaces/{audit['id']}/conversations").get_json()
    assert [c["id"] for c in listed] == [conversation_id]
    assert listed[0]["title"] == "how many rows?"

    payload = client.get(f"/api/conversations/{conversation_id}/turns").get_json()
    assert payload["conversation"]["id"] == conversation_id
    assert payload["conversation"]["workspace_id"] == audit["id"]
    [turn] = payload["turns"]
    assert turn["turn"] == 1 and turn["actor"] == "tester" and turn["action"] == "ask"
    assert turn["question"] == "how many rows?"
    # The same shapes the terminal SSE frame carried.
    assert turn["outcome"] == result["outcome"]
    assert turn["verdict"] == result["verdict"]
    assert turn["status_events"] == result["events"]
    assert turn["tools_used"] == ["query_univariate_stats"]
    assert "univariate_statistics" in turn["substrates_read"]
    assert turn["substrate_versions"]
    assert turn["evidence_bundle_ref"] == result["evidence_bundle_ref"]

    evidence = client.get(f"/api/evidence/{turn['evidence_bundle_ref']}")
    assert evidence.status_code == 200 and evidence.mimetype == "application/json"
    [invocation] = json.loads(evidence.get_data(as_text=True))
    assert invocation["tool"] == "query_univariate_stats"
    assert invocation["status"] == "ok"
    assert client.get("/api/evidence/nonesuch").status_code == 404

    text = client.get(f"/api/conversations/{conversation_id}/turns?format=text")
    assert text.status_code == 200 and text.mimetype == "text/plain"
    body = text.get_data(as_text=True)
    assert body.startswith(f"conversation {conversation_id} · how many rows?\n")
    assert "turn 1 · ✓ Verified · 1 tool" in body
    assert "> how many rows?\nInvoices has 50 rows.\n" in body


def test_a_refused_turn_lists_with_its_card_and_no_diagnosis_in_text(tool_pack):
    app, ports, _ = _real_app(tool_pack, [tool_call("refuse", {"reason": "out of scope", "what_would_work": "a data question"})])
    client = app.test_client()
    frames = parse_frames(client.post("/api/ask", json={"question": "deploy"}).get_data(as_text=True))
    conversation_id = frames[-1][1]["result"]["conversation_id"]
    [turn] = client.get(f"/api/conversations/{conversation_id}/turns").get_json()["turns"]
    assert turn["outcome"]["kind"] == "refuse"
    assert turn["verdict"] is None and turn["evidence_bundle_ref"] is None
    assert turn["status_events"]  # the trail persists for the inspector
    body = client.get(f"/api/conversations/{conversation_id}/turns?format=text").get_data(as_text=True)
    assert "turn 1 · ⊘ Refused · 0 tools" in body
    assert "This can't be answered\nWhy: out of scope\nWhat would work: a data question" in body
