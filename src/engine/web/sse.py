"""Server-sent events over a blocking ask() (Brief §10.2, as amended
in v2.2: status events stream live; the outcome arrives once, after
verification, as the terminal frame — never a token before its
verdict exists).

ask() blocks and its status listener is a synchronous callback, so
the turn runs on a worker thread whose listener enqueues, while the
route's generator drains the queue into SSE frames on the request
thread. Frame contract (README "Web layer"):

  event: status   data: StatusEvent JSON          — live, many
  event: result   data: {exit_code, result}       — terminal, once
  event: error    data: {message}                 — terminal, once
  : keepalive                                     — comment, on silence

Exactly one terminal frame per stream; the generator ends after it.
"""

import json
import queue
import threading
from collections.abc import Iterator

from engine.harness.events import StatusEvent
from engine.harness.outcomes import TurnResult, exit_code_of

KEEPALIVE_FRAME = ": keepalive\n\n"
STATUS = "status"
RESULT = "result"
ERROR = "error"


def encode_frame(event: str, payload: dict) -> str:
    """One SSE frame: a named event with a single JSON data line
    (json.dumps never emits a bare newline, so one line suffices)."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def result_payload(result: TurnResult) -> dict:
    """The terminal frame's data — the same TurnResult JSON `engine ask
    --json` prints, plus the outcome ladder's exit code."""
    return {
        "exit_code": exit_code_of(result.outcome),
        "result": result.model_dump(mode="json"),
    }


def run_turn_stream(
    session,
    question: str,
    conversation_id: int | None,
    *,
    workspace_id: int | None = None,
    keepalive_seconds: float = 15.0,
) -> Iterator[str]:
    """Run one turn on a worker thread and yield its SSE frames.

    The worker never dies silently: an exception becomes the error
    frame, so the generator cannot hang on keepalives. A client that
    disconnects mid-turn raises GeneratorExit here; the worker keeps
    going so the turn's provenance still lands, and the orphaned queue
    is garbage.
    """
    frames: queue.Queue[tuple[str, object]] = queue.Queue()

    def listener(event: StatusEvent) -> None:
        frames.put((STATUS, event))

    def work() -> None:
        try:
            result = session.ask(
                question,
                conversation_id,
                workspace_id=workspace_id,
                listener=listener,
            )
        except Exception as exc:  # the terminal frame reports it
            frames.put((ERROR, exc))
        else:
            frames.put((RESULT, result))

    threading.Thread(target=work, name="engine-ask", daemon=True).start()

    while True:
        try:
            kind, payload = frames.get(timeout=keepalive_seconds)
        except queue.Empty:
            yield KEEPALIVE_FRAME
            continue
        if kind == STATUS:
            yield encode_frame(STATUS, payload.model_dump(mode="json"))
        elif kind == RESULT:
            yield encode_frame(RESULT, result_payload(payload))
            return
        else:
            yield encode_frame(ERROR, {"message": str(payload)})
            return
