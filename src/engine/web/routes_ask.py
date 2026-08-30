"""POST /api/ask and GET /api/config — the Block 1 surface."""

from flask import Blueprint, Response, current_app, jsonify, request

from engine.web.sse import run_turn_stream

bp = Blueprint("ask", __name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # a fronting nginx must not buffer
}


@bp.get("/api/config")
def config():
    """Branding and starter prompts — all from pack config (§10.1);
    the page never knows which pack it serves."""
    ui = current_app.config["ENGINE_UI"]
    identity = current_app.config["ENGINE_IDENTITY"]
    return jsonify(
        {
            "app_name": ui.app_name or current_app.config["ENGINE_PACK_NAME"],
            "accent_color": ui.accent_color,
            "starter_prompts": list(ui.starter_prompts),
            "user": identity.current_user().display_name,
        }
    )


@bp.post("/api/ask")
def ask():
    """One turn as an SSE stream. Everything decidable before the
    stream starts is decided on the request thread with a plain JSON
    status: a bad body (400), an unknown conversation (404), a turn
    already running (409). Then the worker runs session.ask() —
    the Verifier's path, nothing else — and the generator relays."""
    body = request.get_json(silent=True) or {}
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return jsonify({"message": "question must be a non-empty string"}), 400
    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, int):
        return jsonify({"message": "conversation_id must be an integer"}), 400

    work_store = current_app.config["ENGINE_WORK_STORE"]
    session = current_app.config["ENGINE_SESSION"]
    if conversation_id is not None:
        work_store.ensure_schema()
        if work_store.get_conversation(conversation_id) is None:
            return jsonify({"message": f"No conversation {conversation_id}."}), 404
    if session.busy:
        return jsonify({"message": "a turn is already running"}), 409

    keepalive = current_app.config["ENGINE_SSE_KEEPALIVE_SECONDS"]
    stream = run_turn_stream(
        session, question.strip(), conversation_id, keepalive_seconds=keepalive
    )
    return Response(stream, mimetype="text/event-stream", headers=SSE_HEADERS)
