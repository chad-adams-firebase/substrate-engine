"""POST /api/ask: the SSE frame contract (README "Web layer") over a
scripted session, plus one real turn through the graph proving the
route reaches the Verifier. Flask test client only — no browser, no
LLM."""

import json

import pytest

from engine.config.models import PortName, UiSettings
from engine.harness.events import StatusEvent
from engine.harness.outcomes import (
    AnswerOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
    TurnResult,
)
from engine.ports.types import LLMResponse
from engine.tools.envelope import ColumnFormat, Table
from engine.web.app import create_app
from tests.harness_support import build_ask_session, tool_call


class _StubSession:
    def __init__(self, result=None, error=None, busy=False, events=2):
        self._result = result
        self._error = error
        self.busy = busy
        self._events = events
        self.asked = []

    def ask(self, question, conversation_id=None, *, listener=None):
        self.asked.append((question, conversation_id))
        for index in range(self._events):
            listener(
                StatusEvent(
                    node="route",
                    phase="start" if index % 2 == 0 else "finish",
                    detail=f"step {index}",
                    at="2026-05-30T00:00:00+00:00",
                )
            )
        if self._error is not None:
            raise self._error
        return self._result


class _StubStore:
    def __init__(self, known=(7,)):
        self._known = set(known)

    def ensure_schema(self):
        pass

    def get_conversation(self, conversation_id):
        return object() if conversation_id in self._known else None


class _StubIdentity:
    def current_user(self):
        from engine.ports.types import User

        return User(username="dev", display_name="Dev User")


def _result(outcome) -> TurnResult:
    return TurnResult(conversation_id=7, turn=3, outcome=outcome, tools_used=["run_sql"])


def _app(session, store=None, ui=None):
    return create_app(
        session,
        store or _StubStore(),
        _StubIdentity(),
        ui=ui or UiSettings(),
        pack_name="toolpack",
        sse_keepalive_seconds=60,
    )


def parse_frames(text: str) -> list[tuple[str, dict]]:
    """(event, data) per frame; comment-only frames are dropped."""
    frames = []
    for raw in text.split("\n\n"):
        event, data = None, None
        for line in raw.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event is not None:
            frames.append((event, data))
    return frames


def _post(client, question="q", conversation_id=None):
    return client.post(
        "/api/ask", json={"question": question, "conversation_id": conversation_id}
    )


def test_answer_streams_status_frames_then_one_result():
    outcome = AnswerOutcome(body=MarkdownAnswer(text="146 of 161."), verification="verified")
    session = _StubSession(_result(outcome))
    response = _post(_app(session).test_client(), "how many?", 7)

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
    frames = parse_frames(response.get_data(as_text=True))
    assert [event for event, _ in frames] == ["status", "status", "result"]
    assert frames[0][1] == {
        "node": "route", "phase": "start", "detail": "step 0",
        "at": "2026-05-30T00:00:00Z",
    }
    payload = frames[-1][1]
    assert payload["exit_code"] == 0
    assert payload["result"]["outcome"]["body"]["text"] == "146 of 161."
    assert payload["result"]["conversation_id"] == 7
    assert session.asked == [("how many?", 7)]


def test_table_result_carries_money_hints_for_the_browser():
    table = Table(
        columns=["n", "total_opportunity"],
        rows=[{"n": 78, "total_opportunity": 8308.92139244107}],
        total_row_count=1,
        column_formats={"total_opportunity": ColumnFormat(kind="money", symbol="$")},
    )
    outcome = AnswerOutcome(body=TableAnswer(table=table), verification="verified")
    frames = parse_frames(
        _post(_app(_StubSession(_result(outcome))).test_client()).get_data(as_text=True)
    )
    body = frames[-1][1]["result"]["outcome"]["body"]
    assert body["table"]["column_formats"] == {
        "total_opportunity": {"kind": "money", "symbol": "$"}
    }
    # Numbers travel store to screen untouched; the hint formats them.
    assert body["table"]["rows"][0]["total_opportunity"] == 8308.92139244107


def test_refusal_is_a_result_frame_with_its_exit_code():
    outcome = RefuseOutcome(reason="out of scope", what_would_work="a data question")
    frames = parse_frames(
        _post(_app(_StubSession(_result(outcome))).test_client()).get_data(as_text=True)
    )
    assert frames[-1][0] == "result"
    assert frames[-1][1]["exit_code"] == 3
    assert frames[-1][1]["result"]["outcome"] == {
        "kind": "refuse", "reason": "out of scope", "what_would_work": "a data question",
    }


def test_an_exception_becomes_the_error_frame_after_the_trail():
    session = _StubSession(error=RuntimeError("llm unreachable"))
    frames = parse_frames(_post(_app(session).test_client()).get_data(as_text=True))
    assert [event for event, _ in frames] == ["status", "status", "error"]
    assert frames[-1][1] == {"message": "llm unreachable"}


def test_exactly_one_terminal_frame_and_nothing_after_it():
    outcome = AnswerOutcome(body=MarkdownAnswer(text="x"), verification="unverified")
    text = _post(_app(_StubSession(_result(outcome))).test_client()).get_data(as_text=True)
    frames = parse_frames(text)
    terminals = [event for event, _ in frames if event in ("result", "error")]
    assert terminals == ["result"]
    assert frames[-1][0] == "result"
    assert frames[-1][1]["exit_code"] == 2
    assert text.endswith("\n\n")


def test_unknown_conversation_is_404_before_any_stream():
    session = _StubSession(_result(RefuseOutcome(reason="r")))
    response = _post(_app(session).test_client(), "q", 999)
    assert response.status_code == 404
    assert response.get_json() == {"message": "No conversation 999."}
    assert session.asked == []


def test_busy_session_is_409():
    session = _StubSession(_result(RefuseOutcome(reason="r")), busy=True)
    response = _post(_app(session).test_client())
    assert response.status_code == 409
    assert "already running" in response.get_json()["message"]
    assert session.asked == []


@pytest.mark.parametrize(
    "body",
    [{}, {"question": ""}, {"question": "   "}, {"question": 5}, {"question": "q", "conversation_id": "7"}],
)
def test_bad_bodies_are_400(body):
    session = _StubSession(_result(RefuseOutcome(reason="r")))
    response = _app(session).test_client().post("/api/ask", json=body)
    assert response.status_code == 400
    assert session.asked == []


def test_config_comes_from_pack_config_with_pack_name_fallback():
    ui = UiSettings(accent_color="#123456", starter_prompts=["How many?"])
    config = _app(_StubSession(), ui=ui).test_client().get("/api/config").get_json()
    assert config == {
        "app_name": "toolpack",
        "accent_color": "#123456",
        "starter_prompts": ["How many?"],
        "user": "Dev User",
    }
    named = _app(_StubSession(), ui=UiSettings(app_name="Acme Knowledge"))
    assert named.test_client().get("/api/config").get_json()["app_name"] == "Acme Knowledge"


def test_a_real_turn_reaches_the_verifier_and_logs_provenance(tool_pack):
    """The route calls ask() and nothing else: the graph runs, the
    (stub) Verifier is consulted, the turn_log row lands, and the
    terminal frame is the same TurnResult the CLI would print."""
    responses = [
        tool_call("query_univariate_stats", {"table": "invoices", "column": "status"}),
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"),
    ]
    session, ports, verifier = build_ask_session(tool_pack, responses)
    app = create_app(
        session,
        ports.get(PortName.WORK_STORE),
        ports.get(PortName.IDENTITY),
        ui=UiSettings(),
        pack_name="toolpack",
    )
    response = _post(app.test_client(), "how many rows?")
    assert response.status_code == 200
    frames = parse_frames(response.get_data(as_text=True))

    assert frames[-1][0] == "result"
    payload = frames[-1][1]
    assert payload["exit_code"] == 0
    assert payload["result"]["outcome"]["body"]["text"] == "Invoices has 50 rows."
    assert payload["result"]["tools_used"] == ["query_univariate_stats"]
    assert len(verifier.calls) == 1

    nodes = [data["node"] for event, data in frames if event == "status"]
    assert nodes[0] == "route" and "verify" in nodes and nodes[-1] == "finalize"
    assert nodes.index("verify") < nodes.index("finalize")

    store = ports.get(PortName.WORK_STORE)
    conversation_id = payload["result"]["conversation_id"]
    [entry] = store.list_turn_logs(conversation_id)
    assert entry.tools_used == ["query_univariate_stats"]
    assert entry.status_events is not None
    assert not session.busy
