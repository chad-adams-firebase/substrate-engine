"""SQLite adapter for WorkStore — the persistence the engine owns.

stdlib sqlite3: transactional app state with no SQL-dialect concern
(the dialect argument that picked DuckDB for SqlPort does not apply
here — no LLM ever generates SQL against the WorkStore).

ensure_schema creates the full Brief §12 DDL up front so the schema is
designed and reviewed once; Phase 1 only exercises workspace CRUD, and
later phases add operations over the remaining tables as they consume
them. A column added later (turn_log.question / .outcome, Phase 5
Block 3) is migrated in place: CREATE TABLE IF NOT EXISTS leaves an
older table alone, so ensure_schema reads PRAGMA table_info and ALTERs
the missing columns on — a work.db from an earlier block keeps its
rows. Rows are read name-keyed via sqlite3.Row.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from engine.ports.types import Conversation, TurnLogEntry, UnitSummary, Workspace
from engine.ports.work_store import WorkspaceNotEmptyError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id          INTEGER PRIMARY KEY,
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Checkpoint state is NOT a column here: the LangGraph SqliteSaver
-- keeps its own versioned tables in this same database file (see
-- checkpointer()). §12 amendment approved 2026-08-20 — a blob column
-- no code would ever write does not exist.
CREATE TABLE IF NOT EXISTS conversation (
    id            INTEGER PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit_of_work (
    id                      INTEGER PRIMARY KEY,
    workspace_id            INTEGER NOT NULL REFERENCES workspace(id),
    title                   TEXT NOT NULL,
    narrative               TEXT NOT NULL,
    source_turn_refs        TEXT NOT NULL,  -- JSON array, ordered
    provenance_bundle       TEXT,           -- JSON, derived
    substrate_version_refs  TEXT,           -- JSON
    state                   TEXT NOT NULL CHECK (state IN ('draft', 'published', 'canonical')),
    author                  TEXT NOT NULL,
    verifier_verdict        TEXT,           -- publication pass result (Brief §9.5)
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comment (
    id         INTEGER PRIMARY KEY,
    unit_id    INTEGER NOT NULL REFERENCES unit_of_work(id),
    author     TEXT NOT NULL,
    text       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES comment(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_log (
    id                   INTEGER PRIMARY KEY,
    conversation_id      INTEGER NOT NULL REFERENCES conversation(id),
    turn                 INTEGER NOT NULL,
    actor                TEXT NOT NULL,
    action               TEXT NOT NULL,
    tools_used           TEXT,  -- JSON array
    substrates_read      TEXT,  -- JSON array
    evidence_bundle_ref  TEXT,  -- key into evidence_bundle
    verifier_verdict     TEXT,  -- VerifierVerdict JSON, opaque here
    substrate_versions   TEXT,  -- JSON
    -- §12 extension approved 2026-08-20: the per-node start/finish
    -- trail with timestamps (StatusEvent JSON) — what Phase 5's
    -- "Verified · 3 tools · 14s" chip and `engine turns` read.
    status_events        TEXT,
    -- §12 extension (Phase 5 Block 3): the question as asked and the
    -- TurnOutcome JSON, so reopening a conversation shows every turn.
    -- Added by migration on older stores (_TURN_LOG_ADDED_COLUMNS).
    question             TEXT,
    outcome              TEXT,
    created_at           TEXT NOT NULL
);

-- Evidence bundles, content-addressed: ref = sha256 prefix of the
-- canonical TurnEvidence JSON (the manifest_id precedent). Bundle and
-- turn_log row live in one store so they commit together.
CREATE TABLE IF NOT EXISTS evidence_bundle (
    ref        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# Columns added to turn_log after its first release, in the order they
# were added; ensure_schema adds whichever an existing table lacks.
_TURN_LOG_ADDED_COLUMNS = (("question", "TEXT"), ("outcome", "TEXT"))


class SqliteWorkStoreSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Path relative to the pack directory (resolved by the container),
    # or ":memory:" for tests.
    database: str


def _locked(method):
    """Serialize a store method on the instance lock (see __init__)."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class SqliteWorkStore:
    def __init__(self, settings: SqliteWorkStoreSettings) -> None:
        self._settings = settings
        self._lazy_connection: sqlite3.Connection | None = None
        # The web layer runs ask() on a worker thread while request
        # threads read conversations: one connection (a ":memory:"
        # store cannot be per-thread — each thread would get its own
        # empty database) shared across threads, every method holding
        # this lock so statements inside one implicit transaction
        # never interleave. sqlite3.threadsafety is 3 on this build.
        self._lock = threading.RLock()

    @property
    def settings(self) -> SqliteWorkStoreSettings:
        return self._settings

    @property
    def _connection(self) -> sqlite3.Connection:
        # Lazy: constructing the adapter (e.g. for `engine info`) must
        # not create a database file on disk.
        if self._lazy_connection is None:
            database = self._settings.database
            if database != ":memory:":
                Path(database).parent.mkdir(parents=True, exist_ok=True)
            self._lazy_connection = sqlite3.connect(
                database, check_same_thread=False
            )
            self._lazy_connection.row_factory = sqlite3.Row
        return self._lazy_connection

    @_locked
    def ensure_schema(self) -> None:
        with self._connection:
            self._connection.executescript(_SCHEMA)
            present = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(turn_log)"
                ).fetchall()
            }
            for column, sql_type in _TURN_LOG_ADDED_COLUMNS:
                if column not in present:
                    self._connection.execute(
                        f"ALTER TABLE turn_log ADD COLUMN {column} {sql_type}"
                    )

    @_locked
    def create_workspace(self, owner: str, name: str) -> Workspace:
        created_at = datetime.now(UTC)
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO workspace (owner, name, created_at) VALUES (?, ?, ?)",
                (owner, name, created_at.isoformat()),
            )
        return Workspace(
            id=cursor.lastrowid, owner=owner, name=name, created_at=created_at
        )

    @_locked
    def get_workspace(self, workspace_id: int) -> Workspace | None:
        row = self._connection.execute(
            "SELECT id, owner, name, created_at FROM workspace WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        return self._workspace_from(row) if row else None

    @_locked
    def delete_workspace(self, workspace_id: int) -> None:
        with self._connection:
            remaining = self._connection.execute(
                "SELECT COUNT(*) AS n FROM conversation WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()["n"]
            if remaining:
                raise WorkspaceNotEmptyError(
                    f"Workspace {workspace_id} still holds {remaining} "
                    "conversation(s)."
                )
            self._connection.execute(
                "DELETE FROM workspace WHERE id = ?", (workspace_id,)
            )

    @staticmethod
    def _workspace_from(row: sqlite3.Row) -> Workspace:
        return Workspace(
            id=row["id"],
            owner=row["owner"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @_locked
    def create_conversation(self, workspace_id: int, title: str) -> Conversation:
        created_at = datetime.now(UTC)
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO conversation (workspace_id, title, created_at) "
                "VALUES (?, ?, ?)",
                (workspace_id, title, created_at.isoformat()),
            )
        return Conversation(
            id=cursor.lastrowid,
            workspace_id=workspace_id,
            title=title,
            created_at=created_at,
        )

    @_locked
    def get_conversation(self, conversation_id: int) -> Conversation | None:
        row = self._connection.execute(
            "SELECT id, workspace_id, title, created_at FROM conversation "
            "WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return self._conversation_from(row) if row else None

    @_locked
    def list_conversations(self, workspace_id: int) -> list[Conversation]:
        rows = self._connection.execute(
            "SELECT id, workspace_id, title, created_at FROM conversation "
            "WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        return [self._conversation_from(row) for row in rows]

    @_locked
    def rename_conversation(
        self, conversation_id: int, title: str
    ) -> Conversation | None:
        with self._connection:
            self._connection.execute(
                "UPDATE conversation SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
        return self.get_conversation(conversation_id)

    @_locked
    def delete_conversation(self, conversation_id: int) -> None:
        with self._connection:
            # Bundles are content-addressed and may be shared: a second
            # conversation that produced byte-identical evidence points
            # at the same ref, so only bundles no other conversation
            # cites go with this one.
            self._connection.execute(
                "DELETE FROM evidence_bundle WHERE ref IN ("
                "  SELECT evidence_bundle_ref FROM turn_log "
                "  WHERE conversation_id = ? AND evidence_bundle_ref IS NOT NULL"
                ") AND ref NOT IN ("
                "  SELECT evidence_bundle_ref FROM turn_log "
                "  WHERE conversation_id <> ? AND evidence_bundle_ref IS NOT NULL"
                ")",
                (conversation_id, conversation_id),
            )
            self._connection.execute(
                "DELETE FROM turn_log WHERE conversation_id = ?", (conversation_id,)
            )
            self._connection.execute(
                "DELETE FROM conversation WHERE id = ?", (conversation_id,)
            )
        self._delete_checkpoint_thread(conversation_id)

    def _delete_checkpoint_thread(self, conversation_id: int) -> None:
        """The saver owns its tables, so the thread is deleted through
        a saver over the same file, whose connection then closes. A
        ":memory:" store's saver is a separate in-memory database (see
        checkpointer()), so there is nothing to delete there."""
        if self._settings.database == ":memory:":
            return
        saver = self.checkpointer()
        try:
            delete = getattr(saver, "delete_thread", None)
            if delete is not None:
                delete(str(conversation_id))
        finally:
            connection = getattr(saver, "conn", None)
            if connection is not None:
                connection.close()

    @staticmethod
    def _conversation_from(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @_locked
    def append_turn_log(self, entry: TurnLogEntry) -> int:
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO turn_log (conversation_id, turn, actor, action, "
                "tools_used, substrates_read, evidence_bundle_ref, "
                "verifier_verdict, substrate_versions, status_events, "
                "question, outcome, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.conversation_id,
                    entry.turn,
                    entry.actor,
                    entry.action,
                    json.dumps(entry.tools_used),
                    json.dumps(entry.substrates_read),
                    entry.evidence_bundle_ref,
                    entry.verifier_verdict,
                    json.dumps(entry.substrate_versions),
                    entry.status_events,
                    entry.question,
                    entry.outcome,
                    entry.created_at.isoformat(),
                ),
            )
        return cursor.lastrowid

    @_locked
    def list_turn_logs(self, conversation_id: int) -> list[TurnLogEntry]:
        rows = self._connection.execute(
            "SELECT conversation_id, turn, actor, action, tools_used, "
            "substrates_read, evidence_bundle_ref, verifier_verdict, "
            "substrate_versions, status_events, question, outcome, "
            "created_at FROM turn_log "
            "WHERE conversation_id = ? ORDER BY turn, id",
            (conversation_id,),
        ).fetchall()
        return [
            TurnLogEntry(
                conversation_id=row["conversation_id"],
                turn=row["turn"],
                actor=row["actor"],
                action=row["action"],
                tools_used=json.loads(row["tools_used"]),
                substrates_read=json.loads(row["substrates_read"]),
                evidence_bundle_ref=row["evidence_bundle_ref"],
                verifier_verdict=row["verifier_verdict"],
                substrate_versions=json.loads(row["substrate_versions"]),
                status_events=row["status_events"],
                # Pre-Block-3 rows carry NULL in both migrated columns.
                question=row["question"] or "",
                outcome=row["outcome"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @_locked
    def save_evidence_bundle(self, ref: str, payload: str) -> None:
        # Content-addressed, so a duplicate insert is the same bytes:
        # INSERT OR IGNORE makes the write idempotent.
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO evidence_bundle (ref, payload, "
                "created_at) VALUES (?, ?, ?)",
                (ref, payload, datetime.now(UTC).isoformat()),
            )

    @_locked
    def evidence_bundle_visible_to(self, ref: str, owner: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM turn_log t
            JOIN conversation c ON c.id = t.conversation_id
            JOIN workspace w ON w.id = c.workspace_id
            WHERE t.evidence_bundle_ref = ? AND w.owner = ?
            LIMIT 1
            """,
            (ref, owner),
        ).fetchone()
        return row is not None

    @_locked
    def turns_without_question(self) -> list[tuple[int, int]]:
        rows = self._connection.execute(
            "SELECT conversation_id, turn FROM turn_log "
            "WHERE question IS NULL OR question = '' "
            "ORDER BY conversation_id, turn"
        ).fetchall()
        return [(row["conversation_id"], row["turn"]) for row in rows]

    @_locked
    def set_turn_question(self, conversation_id: int, turn: int, question: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE turn_log SET question = ? "
                "WHERE conversation_id = ? AND turn = ? "
                "AND (question IS NULL OR question = '')",
                (question, conversation_id, turn),
            )

    @_locked
    def load_evidence_bundle(self, ref: str) -> str | None:
        row = self._connection.execute(
            "SELECT payload FROM evidence_bundle WHERE ref = ?", (ref,)
        ).fetchone()
        return row["payload"] if row else None

    @staticmethod
    def checkpoint_serde() -> Any:
        """The checkpoint serializer with every engine type it round-
        trips registered up front (Addendum N8): LangGraph's msgpack
        deserialization warns on unregistered types today and will
        refuse them once strict becomes the default. The allowlist
        takes exact classes, no module prefixes; the round-trip test
        in tests/test_harness_checkpoint.py is what forces this list
        to grow with the state schema."""
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        from engine.config.models import SubstrateName, ToolName
        from engine.harness.outcomes import (
            AnswerOutcome,
            ClarifyOutcome,
            EscalateOutcome,
            MarkdownAnswer,
            RefuseOutcome,
            TableAnswer,
        )
        from engine.harness.state import (
            HistoryTurn,
            RouteDecision,
            ToolSelection,
            TurnState,
        )
        from engine.ports.types import Message, ToolCall
        from engine.tools.envelope import ToolInvocation
        from engine.verifier.models import (
            AttemptRecord,
            InjectedSpan,
            PlausibilityRecord,
            VerifierVerdict,
        )

        return JsonPlusSerializer(
            allowed_msgpack_modules=[
                Message,
                ToolCall,  # nested in the scratch messages' tool_calls
                HistoryTurn,
                ToolName,
                SubstrateName,
                ToolInvocation,
                RouteDecision,
                ToolSelection,
                TurnState,
                MarkdownAnswer,
                TableAnswer,
                AnswerOutcome,
                RefuseOutcome,
                ClarifyOutcome,
                EscalateOutcome,
                AttemptRecord,
                PlausibilityRecord,
                VerifierVerdict,
                InjectedSpan,
            ]
        )

    def checkpointer(self) -> Any:
        """The LangGraph SqliteSaver, against this store's database
        file. The saver owns its checkpoints/writes tables alongside
        the §12 tables — one gitignored work.db to inspect. A second
        connection is fine for this single-process, sequential
        workload; the real adapter swaps in the Postgres-saver pattern
        behind this same method (Brief §2).

        The langgraph import lives here, in the adapter, because the
        harness core must not know which saver persists it. Note a
        ":memory:" database yields a saver over a SEPARATE in-memory
        store — fine for tests, which use a file when checkpoint
        persistence itself is under test.
        """
        from langgraph.checkpoint.sqlite import SqliteSaver

        database = self._settings.database
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        return SqliteSaver(
            sqlite3.connect(database, check_same_thread=False),
            serde=self.checkpoint_serde(),
        )

    @_locked
    def search_published_units(self, text: str) -> list[UnitSummary]:
        # LIKE over title+narrative is deliberately all there is —
        # Phase 6 owns anything smarter. ensure_schema first: this is
        # the one read that legitimately runs against a store nothing
        # has written to yet (the library is empty until Phase 6), and
        # it must return [], not "no such table".
        self.ensure_schema()
        like = f"%{text}%"
        rows = self._connection.execute(
            "SELECT id, title, narrative, state, author FROM unit_of_work "
            "WHERE state IN ('published', 'canonical') "
            "AND (title LIKE ? OR narrative LIKE ?) ORDER BY id",
            (like, like),
        ).fetchall()
        return [
            UnitSummary(
                id=row["id"],
                title=row["title"],
                state=row["state"],
                author=row["author"],
                snippet=row["narrative"][:200],
            )
            for row in rows
        ]

    @_locked
    def list_workspaces(self, owner: str) -> list[Workspace]:
        rows = self._connection.execute(
            "SELECT id, owner, name, created_at FROM workspace "
            "WHERE owner = ? ORDER BY id",
            (owner,),
        ).fetchall()
        return [self._workspace_from(row) for row in rows]
