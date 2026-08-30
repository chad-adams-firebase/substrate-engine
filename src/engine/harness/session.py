"""AskSession: the turn lifecycle around the graph.

One ask() = schema bootstrap, conversation resolution, graph.invoke
against the conversation's checkpoint thread, then provenance — the
evidence bundle write and the §12 turn_log row — AFTER the graph
returns, so a crash mid-turn leaves no half-row. The graph never
touches WorkStore; this is the single writer.
"""

import hashlib
import threading
from datetime import UTC, datetime

from engine.harness.events import EventLog, StatusListener
from engine.harness.graph import GraphDeps, build_graph
from engine.harness.outcomes import TurnResult
from engine.harness.state import TurnState
from engine.ports.identity import IdentityPort
from engine.ports.types import Conversation, TurnLogEntry
from engine.ports.work_store import WorkStorePort
from engine.tools.envelope import dumps_turn_evidence

SCRATCH_WORKSPACE = "scratch"


class UnknownConversationError(Exception):
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
        self, conversation_id: int | None, question: str
    ) -> Conversation:
        if conversation_id is not None:
            conversation = self._work_store.get_conversation(conversation_id)
            if conversation is None:
                raise UnknownConversationError(
                    f"No conversation {conversation_id}."
                )
            return conversation
        owner = self._identity.current_user().username
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
        return self._work_store.create_conversation(
            workspace.id, question[:60]
        )

    def ask(
        self,
        question: str,
        conversation_id: int | None = None,
        *,
        listener: StatusListener | None = None,
    ) -> TurnResult:
        """One turn. `listener` receives this call's status events live
        (an SSE queue); omitted, the session's constructor listener
        (the CLI's stderr trail) does. Raises TurnInProgressError
        instead of waiting if another turn holds the session."""
        if not self._turn_lock.acquire(blocking=False):
            raise TurnInProgressError("A turn is already running.")
        try:
            return self._ask(question, conversation_id, listener or self._listener)
        finally:
            self._turn_lock.release()

    def _ask(
        self,
        question: str,
        conversation_id: int | None,
        listener: StatusListener | None,
    ) -> TurnResult:
        self._work_store.ensure_schema()
        conversation = self._resolve_conversation(conversation_id, question)

        events = EventLog(listener)
        # Assigned only under the turn lock: nodes read deps.events by
        # closure, so two turns on one session would cross-wire trails.
        self._deps.events = events
        config = {"configurable": {"thread_id": str(conversation.id)}}
        raw_state = self._graph.invoke({"question": question}, config)
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
        )
