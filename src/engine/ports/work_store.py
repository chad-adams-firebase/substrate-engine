"""WorkStore — persistence the engine OWNS (Brief §12), as opposed to
substrates it READS.

Local adapter: SQLite. Real adapter (later phase): Delta tables +
Postgres-saver checkpointer pattern.

Phase 1 exposed schema bootstrap plus workspace CRUD. Phase 3 added
search_published_units (the answer_from_known_items read; the library
is legitimately empty until Phase 6 publishes into it). Phase 4 adds
what the ask path consumes: conversation CRUD, the §12 turn_log
append/read, evidence-bundle storage (content-addressed refs pointing
at canonical TurnEvidence JSON), and the LangGraph checkpointer.
Unit-of-Work and comment operations remain Phase 6.
"""

from typing import Any, Protocol

from engine.ports.types import Conversation, TurnLogEntry, UnitSummary, Workspace


class WorkStorePort(Protocol):
    def ensure_schema(self) -> None: ...

    def create_workspace(self, owner: str, name: str) -> Workspace: ...

    def list_workspaces(self, owner: str) -> list[Workspace]: ...

    def create_conversation(
        self, workspace_id: int, title: str
    ) -> Conversation: ...

    def get_conversation(self, conversation_id: int) -> Conversation | None: ...

    def list_conversations(self, workspace_id: int) -> list[Conversation]: ...

    def append_turn_log(self, entry: TurnLogEntry) -> int: ...

    def list_turn_logs(self, conversation_id: int) -> list[TurnLogEntry]: ...

    def save_evidence_bundle(self, ref: str, payload: str) -> None: ...

    def load_evidence_bundle(self, ref: str) -> str | None: ...

    def checkpointer(self) -> Any:
        """A LangGraph BaseCheckpointSaver persisting to this store's
        backing storage. Typed Any because ports import only ports,
        substrate models, and stdlib — the concrete saver class lives
        with the adapter."""
        ...

    def search_published_units(self, text: str) -> list[UnitSummary]: ...
