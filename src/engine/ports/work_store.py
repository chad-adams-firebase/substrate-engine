"""WorkStore — persistence the engine OWNS (Brief §12), as opposed to
substrates it READS.

Local adapter: SQLite. Real adapter (later phase): Delta tables +
Postgres-saver checkpointer pattern.

Phase 1 exposes schema bootstrap plus workspace CRUD — the smallest
slice that proves persistence works. Conversation, Unit-of-Work,
comment, and turn-log operations are added by the phases that consume
them (4–6); the §12 DDL itself is created in full by ensure_schema so
the schema is designed and reviewed once.

Phase 3 adds search_published_units — the one read the
answer_from_known_items tool needs. Publication itself is Phase 6;
until then the library is legitimately empty and the search
legitimately returns nothing.
"""

from typing import Protocol

from engine.ports.types import UnitSummary, Workspace


class WorkStorePort(Protocol):
    def ensure_schema(self) -> None: ...

    def create_workspace(self, owner: str, name: str) -> Workspace: ...

    def list_workspaces(self, owner: str) -> list[Workspace]: ...

    def search_published_units(self, text: str) -> list[UnitSummary]: ...
