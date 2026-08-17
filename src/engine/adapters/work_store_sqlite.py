"""SQLite adapter for WorkStore — the persistence the engine owns.

stdlib sqlite3: transactional app state with no SQL-dialect concern
(the dialect argument that picked DuckDB for SqlPort does not apply
here — no LLM ever generates SQL against the WorkStore).

ensure_schema creates the full Brief §12 DDL up front so the schema is
designed and reviewed once; Phase 1 only exercises workspace CRUD, and
later phases add operations over the remaining tables as they consume
them. Rows are read name-keyed via sqlite3.Row.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from engine.ports.types import UnitSummary, Workspace

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id          INTEGER PRIMARY KEY,
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
    id            INTEGER PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspace(id),
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    checkpoint    BLOB
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
    evidence_bundle_ref  TEXT,
    verifier_verdict     TEXT,
    substrate_versions   TEXT,  -- JSON
    created_at           TEXT NOT NULL
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
