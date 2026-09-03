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
Phase 5 Block 3 adds what the workspace sidebar consumes: workspace
lookup and deletion, conversation rename and deletion (the turn log,
the bundles only that conversation references, and the checkpoint
thread go with it). Unit-of-Work and comment operations remain
Phase 6.
"""

from typing import Any, Protocol

from engine.ports.types import Conversation, TurnLogEntry, UnitSummary, Workspace


class WorkspaceNotEmptyError(Exception):
    """delete_workspace on a workspace that still holds conversations.
    Deleting a folder never deletes its contents implicitly."""


class WorkStorePort(Protocol):
    def ensure_schema(self) -> None:
        """Create the §12 tables, and bring an older store's tables up
        to the current columns in place (a work.db from an earlier
        block keeps its rows)."""
        ...

    def create_workspace(self, owner: str, name: str) -> Workspace: ...

    def get_workspace(self, workspace_id: int) -> Workspace | None: ...

    def list_workspaces(self, owner: str) -> list[Workspace]: ...

    def delete_workspace(self, workspace_id: int) -> None:
        """Raises WorkspaceNotEmptyError while conversations remain.
        A missing workspace is a no-op."""
        ...

    def create_conversation(
        self, workspace_id: int, title: str
    ) -> Conversation: ...

    def get_conversation(self, conversation_id: int) -> Conversation | None: ...

    def list_conversations(self, workspace_id: int) -> list[Conversation]: ...

    def rename_conversation(
        self, conversation_id: int, title: str
    ) -> Conversation | None: ...

    def delete_conversation(self, conversation_id: int) -> None:
        """The conversation, its turn_log rows, the evidence bundles no
        other conversation references, and its checkpoint thread. A
        missing conversation is a no-op."""
        ...

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
