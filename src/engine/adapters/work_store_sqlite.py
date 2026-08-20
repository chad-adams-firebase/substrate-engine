"""SQLite adapter for WorkStore — the persistence the engine owns.

stdlib sqlite3: transactional app state with no SQL-dialect concern
(the dialect argument that picked DuckDB for SqlPort does not apply
here — no LLM ever generates SQL against the WorkStore).

ensure_schema creates the full Brief §12 DDL up front so the schema is
designed and reviewed once; Phase 1 only exercises workspace CRUD, and
later phases add operations over the remaining tables as they consume
them. Rows are read name-keyed via sqlite3.Row.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from engine.ports.types import Conversation, TurnLogEntry, UnitSummary, Workspace

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


class SqliteWorkStoreSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Path relative to the pack directory (resolved by the container),
    # or ":memory:" for tests.
    database: str


class SqliteWorkStore:
    def __init__(self, settings: SqliteWorkStoreSettings) -> None:
        self._settings = settings
        self._lazy_connection: sqlite3.Connection | None = None

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
            self._lazy_connection = sqlite3.connect(database)
            self._lazy_connection.row_factory = sqlite3.Row
        return self._lazy_connection

    def ensure_schema(self) -> None:
        with self._connection:
            self._connection.executescript(_SCHEMA)

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

    def get_conversation(self, conversation_id: int) -> Conversation | None:
        row = self._connection.execute(
            "SELECT id, workspace_id, title, created_at FROM conversation "
            "WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return self._conversation_from(row) if row else None

    def list_conversations(self, workspace_id: int) -> list[Conversation]:
        rows = self._connection.execute(
            "SELECT id, workspace_id, title, created_at FROM conversation "
            "WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        return [self._conversation_from(row) for row in rows]

    @staticmethod
    def _conversation_from(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def append_turn_log(self, entry: TurnLogEntry) -> int:
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO turn_log (conversation_id, turn, actor, action, "
                "tools_used, substrates_read, evidence_bundle_ref, "
                "verifier_verdict, substrate_versions, status_events, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    entry.created_at.isoformat(),
                ),
            )
        return cursor.lastrowid

    def list_turn_logs(self, conversation_id: int) -> list[TurnLogEntry]:
        rows = self._connection.execute(
            "SELECT conversation_id, turn, actor, action, tools_used, "
            "substrates_read, evidence_bundle_ref, verifier_verdict, "
            "substrate_versions, status_events, created_at FROM turn_log "
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
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def save_evidence_bundle(self, ref: str, payload: str) -> None:
        # Content-addressed, so a duplicate insert is the same bytes:
        # INSERT OR IGNORE makes the write idempotent.
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO evidence_bundle (ref, payload, "
                "created_at) VALUES (?, ?, ?)",
                (ref, payload, datetime.now(UTC).isoformat()),
            )

    def load_evidence_bundle(self, ref: str) -> str | None:
        row = self._connection.execute(
            "SELECT payload FROM evidence_bundle WHERE ref = ?", (ref,)
        ).fetchone()
        return row["payload"] if row else None

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
            sqlite3.connect(database, check_same_thread=False)
        )

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

    def list_workspaces(self, owner: str) -> list[Workspace]:
        rows = self._connection.execute(
            "SELECT id, owner, name, created_at FROM workspace "
            "WHERE owner = ? ORDER BY id",
            (owner,),
        ).fetchall()
        return [
            Workspace(
                id=row["id"],
                owner=row["owner"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
