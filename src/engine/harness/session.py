"""AskSession: the turn lifecycle around the graph.

One ask() = schema bootstrap, conversation resolution, graph.invoke
against the conversation's checkpoint thread, then provenance — the
evidence bundle write and the §12 turn_log row — AFTER the graph
returns, so a crash mid-turn leaves no half-row. A conversation this
call created is deleted again if the graph raises, so a failed first
turn leaves no orphan conversation either (Phase 5 Block 3: the play
sessions' zero-turn rows). The graph never touches WorkStore; this is
the single writer.
"""

import hashlib
import threading
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from engine.config.models import ContextSettings
from engine.harness.events import EventLog, StatusListener
from engine.harness.graph import GraphDeps, build_graph
from engine.harness.outcomes import TurnResult, dumps_outcome
from engine.harness.state import TurnState
from engine.ports.identity import IdentityPort
from engine.ports.types import Conversation, TurnLogEntry
from engine.ports.work_store import WorkStorePort
from engine.tools.envelope import dumps_turn_evidence

SCRATCH_WORKSPACE = "scratch"


class UnknownConversationError(Exception):
    pass


class UnknownWorkspaceError(Exception):
    pass


class TurnInProgressError(Exception):
    """A second ask() while one is running. One turn in flight per
    session, by design: the graph's per-turn EventLog, the saver
    connection, and the WorkStore connection are all singular, so the
    web layer serializes (409) rather than interleaving."""


def evidence_ref_of(payload: str) -> str:
    """Content-addressed bundle ref — sha256 prefix over the canonical
    JSON, the manifest_id precedent."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ConversationContext(BaseModel):
    """A conversation's running summary as the checkpoint holds it
    (Brief §10.3) — what the page's banner and inspector read."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    summary_through_turn: int = 0


class AskSession:
    def __init__(
        self,
        *,
        deps: GraphDeps,
        work_store: WorkStorePort,
        identity: IdentityPort,
        listener: StatusListener | None = None,
    ) -> None:
        self._deps = deps
        self._work_store = work_store
        self._identity = identity
        self._listener = listener
        self._graph = build_graph(deps, checkpointer=work_store.checkpointer())
        self._turn_lock = threading.Lock()

    @property
    def busy(self) -> bool:
        """True while a turn is running — the web route's 409 check."""
        return self._turn_lock.locked()

    def _resolve_conversation(
        self,
        conversation_id: int | None,
        workspace_id: int | None,
        question: str,
    ) -> tuple[Conversation, bool]:
        """(conversation, created). Continuing names a conversation; a
        new one lands in the named workspace, or in the owner's scratch
        workspace (created on first use) when none is named."""
        if conversation_id is not None:
            conversation = self._work_store.get_conversation(conversation_id)
            if conversation is None:
                raise UnknownConversationError(
                    f"No conversation {conversation_id}."
                )
            return conversation, False
        owner = self._identity.current_user().username
        if workspace_id is not None:
            workspace = self._work_store.get_workspace(workspace_id)
            if workspace is None or workspace.owner != owner:
                raise UnknownWorkspaceError(f"No workspace {workspace_id}.")
        else:
            workspaces = [
                w
                for w in self._work_store.list_workspaces(owner)
                if w.name == SCRATCH_WORKSPACE
            ]
            workspace = (
                workspaces[0]
                if workspaces
                else self._work_store.create_workspace(owner, SCRATCH_WORKSPACE)
            )
        conversation = self._work_store.create_conversation(
            workspace.id, question[:60]
        )
        return conversation, True

    def ask(
        self,
        question: str,
        conversation_id: int | None = None,
        *,
        workspace_id: int | None = None,
        listener: StatusListener | None = None,
        context: ContextSettings | None = None,
    ) -> TurnResult:
        """One turn. `workspace_id` places a NEW conversation (ignored
        when conversation_id continues one). `listener` receives this
        call's status events live (an SSE queue); omitted, the
        session's constructor listener (the CLI's stderr trail) does.
        `context` overrides the pack's context window and summary
        cadence for this turn only (the eval bank's per-row override);
        omitted, the pack's settings apply. Raises TurnInProgressError
        instead of waiting if another turn holds the session."""
        if not self._turn_lock.acquire(blocking=False):
            raise TurnInProgressError("A turn is already running.")
        try:
            return self._ask(
                question,
                conversation_id,
                workspace_id,
                listener or self._listener,
                context,
            )
        finally:
            self._turn_lock.release()

    def context_of(self, conversation_id: int) -> ConversationContext:
        """The running summary the conversation's checkpoint holds —
        a read through the saver, safe beside a running turn."""
        snapshot = self._graph.get_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        values = snapshot.values or {}
        return ConversationContext(
            summary=values.get("summary", ""),
            summary_through_turn=values.get("summary_through_turn", 0),
        )

    def _ask(
        self,
        question: str,
        conversation_id: int | None,
        workspace_id: int | None,
        listener: StatusListener | None,
        context: ContextSettings | None,
    ) -> TurnResult:
        self._work_store.ensure_schema()
        conversation, created = self._resolve_conversation(
            conversation_id, workspace_id, question
        )

        events = EventLog(listener)
        # Assigned only under the turn lock: nodes read deps.events and
        # deps.context by closure, so two turns on one session would
        # cross-wire trails and windows.
        self._deps.events = events
        self._deps.context = (
            context if context is not None else self._deps.settings.context
        )
        config = {"configurable": {"thread_id": str(conversation.id)}}
        try:
            raw_state = self._graph.invoke({"question": question}, config)
        except BaseException:
            # No turn_log row will land, so a conversation this call
            # opened would sit empty forever; take it (and whatever
            # partial checkpoint the graph wrote) back out.
            if created:
                self._work_store.delete_conversation(conversation.id)
            raise
        state = (
            TurnState.model_validate(raw_state)
            if isinstance(raw_state, dict)
            else raw_state
        )

        evidence_ref = None
        if state.evidence:
            payload = dumps_turn_evidence(state.evidence)
            evidence_ref = evidence_ref_of(payload)
            self._work_store.save_evidence_bundle(evidence_ref, payload)

        substrates_read = sorted(
            {
                name.value
                for invocation in state.evidence
                for name in invocation.substrates_read
            }
        )
        substrate_versions = sorted(
            {
                manifest_id
                for invocation in state.evidence
                for manifest_id in invocation.manifest_ids
            }
        )
        self._work_store.append_turn_log(
            TurnLogEntry(
                conversation_id=conversation.id,
                turn=state.turn,
                actor=self._identity.current_user().username,
                action="ask",
                tools_used=[inv.tool.value for inv in state.evidence],
                substrates_read=substrates_read,
                evidence_bundle_ref=evidence_ref,
                verifier_verdict=(
                    state.verdict.model_dump_json()
                    if state.verdict is not None
                    else None
                ),
                substrate_versions=substrate_versions,
                status_events=events.dump_json(),
                question=question,
                outcome=(
                    dumps_outcome(state.outcome)
                    if state.outcome is not None
                    else None
                ),
                created_at=datetime.now(UTC),
            )
        )
        return TurnResult(
            conversation_id=conversation.id,
            turn=state.turn,
            outcome=state.outcome,
            tools_used=[inv.tool.value for inv in state.evidence],
            evidence_bundle_ref=evidence_ref,
            verdict=state.verdict,
            events=events.events,
            summary=state.summary,
            summary_through_turn=state.summary_through_turn,
        )
