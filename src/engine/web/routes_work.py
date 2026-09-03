"""Workspaces, conversations, turns, evidence — the Block 3 surface
behind the sidebar and the inspector (Brief §10.1, §10.4).

Owner scoping: every workspace a route touches must belong to
identity.current_user(); another user's workspace — or a conversation
inside one — is 404, never 403 (its existence is not this user's
business). The default scratch workspace is created on the first
listing, the same name AskSession uses when no workspace is named.

No engine logic here: the store is read and written through the port,
outcomes and verdicts pass through as the JSON the turn log holds, and
the text form renders through engine.web.render, the page's twin.
"""

import json

from flask import Blueprint, Response, current_app, jsonify, request

from engine.harness.session import SCRATCH_WORKSPACE
from engine.ports.types import Conversation, TurnLogEntry, Workspace
from engine.ports.work_store import WorkspaceNotEmptyError
from engine.web.render import render_turns_text

bp = Blueprint("work", __name__)


def _store():
    return current_app.config["ENGINE_WORK_STORE"]


def _owner() -> str:
    return current_app.config["ENGINE_IDENTITY"].current_user().username


@bp.before_request
def _schema() -> None:
    # Every route here reads the §12 tables; a fresh work.db has none
    # until something bootstraps it, and the sidebar loads before any
    # turn runs.
    _store().ensure_schema()


def _owned_workspace(workspace_id: int) -> Workspace | None:
    workspace = _store().get_workspace(workspace_id)
    if workspace is None or workspace.owner != _owner():
        return None
    return workspace


def _owned_conversation(conversation_id: int) -> Conversation | None:
    conversation = _store().get_conversation(conversation_id)
    if conversation is None or _owned_workspace(conversation.workspace_id) is None:
        return None
    return conversation


def _title_from_body() -> str | None:
    body = request.get_json(silent=True) or {}
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


def _not_found(what: str, identifier: int | str):
    return jsonify({"message": f"No {what} {identifier}."}), 404


# --- workspaces --------------------------------------------------------


@bp.get("/api/workspaces")
def list_workspaces():
    store = _store()
    owner = _owner()
    workspaces = store.list_workspaces(owner)
    if not workspaces:
        workspaces = [store.create_workspace(owner, SCRATCH_WORKSPACE)]
    return jsonify([w.model_dump(mode="json") for w in workspaces])


@bp.post("/api/workspaces")
def create_workspace():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"message": "name must be a non-empty string"}), 400
    workspace = _store().create_workspace(_owner(), name.strip())
    return jsonify(workspace.model_dump(mode="json")), 201


@bp.delete("/api/workspaces/<int:workspace_id>")
def delete_workspace(workspace_id: int):
    if _owned_workspace(workspace_id) is None:
        return _not_found("workspace", workspace_id)
    try:
        _store().delete_workspace(workspace_id)
    except WorkspaceNotEmptyError as exc:
        return jsonify({"message": str(exc)}), 409
    return "", 204


# --- conversations -----------------------------------------------------


@bp.get("/api/workspaces/<int:workspace_id>/conversations")
def list_conversations(workspace_id: int):
    if _owned_workspace(workspace_id) is None:
        return _not_found("workspace", workspace_id)
    conversations = _store().list_conversations(workspace_id)
    return jsonify([c.model_dump(mode="json") for c in conversations])


@bp.post("/api/workspaces/<int:workspace_id>/conversations")
def create_conversation(workspace_id: int):
    if _owned_workspace(workspace_id) is None:
        return _not_found("workspace", workspace_id)
    title = _title_from_body()
    if title is None:
        return jsonify({"message": "title must be a non-empty string"}), 400
    conversation = _store().create_conversation(workspace_id, title)
    return jsonify(conversation.model_dump(mode="json")), 201


@bp.patch("/api/conversations/<int:conversation_id>")
def rename_conversation(conversation_id: int):
    if _owned_conversation(conversation_id) is None:
        return _not_found("conversation", conversation_id)
    title = _title_from_body()
    if title is None:
        return jsonify({"message": "title must be a non-empty string"}), 400
    renamed = _store().rename_conversation(conversation_id, title)
    return jsonify(renamed.model_dump(mode="json"))


@bp.delete("/api/conversations/<int:conversation_id>")
def delete_conversation(conversation_id: int):
    if _owned_conversation(conversation_id) is None:
        return _not_found("conversation", conversation_id)
    # One turn runs per process; deleting a conversation from under a
    # turn that may be writing to it is refused rather than raced.
    if current_app.config["ENGINE_SESSION"].busy:
        return jsonify({"message": "a turn is already running"}), 409
    _store().delete_conversation(conversation_id)
    return "", 204


# --- turns and evidence ------------------------------------------------


def _turn_json(entry: TurnLogEntry) -> dict:
    """One turn as the page reads it: the §12 row with its JSON columns
    parsed (outcome, verdict, status events) — TurnOutcome,
    VerifierVerdict and StatusEvent shapes, the same the terminal SSE
    frame carries, so a reopened turn renders through the same code."""
    return {
        "turn": entry.turn,
        "actor": entry.actor,
        "action": entry.action,
        "question": entry.question,
        "outcome": json.loads(entry.outcome) if entry.outcome else None,
        "tools_used": list(entry.tools_used),
        "substrates_read": list(entry.substrates_read),
        "substrate_versions": list(entry.substrate_versions),
        "verdict": (
            json.loads(entry.verifier_verdict) if entry.verifier_verdict else None
        ),
        "status_events": (
            json.loads(entry.status_events) if entry.status_events else []
        ),
        "evidence_bundle_ref": entry.evidence_bundle_ref,
        "created_at": entry.created_at.isoformat(),
    }


@bp.get("/api/conversations/<int:conversation_id>/turns")
def list_turns(conversation_id: int):
    """The conversation and every logged turn. `?format=text` renders
    the same rows as a plain-text transcript (engine.web.render), the
    page's twin for a terminal or a test."""
    conversation = _owned_conversation(conversation_id)
    if conversation is None:
        return _not_found("conversation", conversation_id)
    entries = _store().list_turn_logs(conversation_id)
    if request.args.get("format") == "text":
        return Response(
            render_turns_text(conversation, entries),
            mimetype="text/plain; charset=utf-8",
        )
    return jsonify(
        {
            "conversation": conversation.model_dump(mode="json"),
            "turns": [_turn_json(entry) for entry in entries],
        }
    )


@bp.get("/api/evidence/<ref>")
def evidence(ref: str):
    """The evidence bundle behind a turn: the canonical TurnEvidence
    JSON exactly as stored (content-addressed, so the bytes are the
    ref's proof). Fetched by the inspector on demand — bundles are
    large and most turns are never inspected."""
    payload = _store().load_evidence_bundle(ref)
    if payload is None:
        return _not_found("evidence bundle", ref)
    return Response(payload, mimetype="application/json")
