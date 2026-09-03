"""SQLite WorkStore adapter: schema bootstrap is idempotent, workspace
and conversation CRUD round-trip, turn logs and evidence bundles
persist, the checkpointer stays lazy, and an older store migrates in
place (Phase 5 Block 3)."""

import sqlite3
from datetime import UTC, datetime

import pytest

from engine.adapters.work_store_sqlite import (
    SqliteWorkStore,
    SqliteWorkStoreSettings,
)
from engine.ports.types import TurnLogEntry
from engine.ports.work_store import WorkspaceNotEmptyError


@pytest.fixture
def store():
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=":memory:"))
    store.ensure_schema()
    return store


def test_ensure_schema_is_idempotent(store):
    store.ensure_schema()  # second call must not raise or clobber
    store.create_workspace("chad", "scratch")
    store.ensure_schema()  # nor a third, with data present

    assert [w.name for w in store.list_workspaces("chad")] == ["scratch"]


def test_workspace_round_trip(store):
    created = store.create_workspace("chad", "scratch")

    listed = store.list_workspaces("chad")

    assert len(listed) == 1
    assert listed[0] == created


def test_workspaces_are_scoped_to_owner(store):
    store.create_workspace("chad", "scratch")
    store.create_workspace("dana", "audit-notes")

    assert [w.name for w in store.list_workspaces("chad")] == ["scratch"]
    assert [w.name for w in store.list_workspaces("dana")] == ["audit-notes"]


def test_full_section_12_schema_exists(store):
    """ensure_schema creates the whole Brief §12 DDL up front — later
    phases add operations, not tables."""
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert {
        "workspace",
        "conversation",
        "unit_of_work",
        "comment",
        "turn_log",
        "evidence_bundle",
    } <= tables

    # The checkpointer's tables are NOT ours: SqliteSaver creates its
    # own alongside (approved §12 amendment — no checkpoint blob here).
    columns = {
        row["name"]
        for row in store._connection.execute(
            "PRAGMA table_info(conversation)"
        ).fetchall()
    }
    assert "checkpoint" not in columns


def test_conversation_round_trip(store):
    workspace = store.create_workspace("chad", "scratch")
    created = store.create_conversation(workspace.id, "flag rates")

    assert store.get_conversation(created.id) == created
    assert store.get_conversation(9999) is None
    other = store.create_conversation(workspace.id, "second thread")
    assert [c.title for c in store.list_conversations(workspace.id)] == [
        "flag rates",
        "second thread",
    ]
    assert other.id != created.id


def _entry(conversation_id: int, turn: int, **overrides) -> TurnLogEntry:
    fields = {
        "conversation_id": conversation_id,
        "turn": turn,
        "actor": "dev",
        "action": "ask",
        "tools_used": ["run_sql"],
        "substrates_read": ["application_database", "data_dictionary"],
        "evidence_bundle_ref": "abc123",
        "verifier_verdict": '{"disposition": "verified"}',
        "substrate_versions": ["m1", "m2"],
        "status_events": "[]",
        "question": "how many?",
        "outcome": '{"kind": "refuse", "reason": "r"}',
        "created_at": datetime(2026, 5, 30, tzinfo=UTC),
    }
    fields.update(overrides)
    return TurnLogEntry(**fields)


def test_turn_log_round_trips_every_field(store):
    workspace = store.create_workspace("chad", "scratch")
    conversation = store.create_conversation(workspace.id, "t")
    entry = _entry(conversation.id, 1)
    row_id = store.append_turn_log(entry)

    assert row_id > 0
    assert store.list_turn_logs(conversation.id) == [entry]


def test_turn_logs_ordered_by_turn_and_nullable_fields_survive(store):
    workspace = store.create_workspace("chad", "scratch")
    conversation = store.create_conversation(workspace.id, "t")
    # A refuse turn legitimately has no bundle, verdict, or tools.
    refusal = _entry(
        conversation.id,
        2,
        tools_used=[],
        evidence_bundle_ref=None,
        verifier_verdict=None,
        status_events=None,
    )
    store.append_turn_log(refusal)
    store.append_turn_log(_entry(conversation.id, 1))

    logs = store.list_turn_logs(conversation.id)
    assert [log.turn for log in logs] == [1, 2]
    assert logs[1] == refusal


def test_evidence_bundle_save_is_idempotent(store):
    store.save_evidence_bundle("ref1", '{"a": 1}')
    store.save_evidence_bundle("ref1", '{"a": 1}')  # same bytes, no error

    assert store.load_evidence_bundle("ref1") == '{"a": 1}'
    assert store.load_evidence_bundle("missing") is None


def test_checkpointer_round_trips_through_the_store_file(tmp_path):
    database = tmp_path / "work.db"
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=str(database)))
    store.ensure_schema()

    saver = store.checkpointer()
    config = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "id": "chk-1",
        "ts": "2026-05-30T00:00:00+00:00",
        "channel_values": {"history": ["hello"]},
        "channel_versions": {},
        "versions_seen": {},
    }
    saver.put(config, checkpoint, {}, {})

    # A fresh saver over the same file sees the checkpoint — and the
    # §12 tables are untouched neighbors in the same database.
    reread = store.checkpointer().get(config)
    assert reread is not None
    assert reread["channel_values"] == {"history": ["hello"]}
    tables = {
        row["name"]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "checkpoints" in tables and "turn_log" in tables


def test_constructing_the_adapter_touches_no_file(tmp_path):
    database = tmp_path / "sub" / "work.db"
    SqliteWorkStore(SqliteWorkStoreSettings(database=str(database)))
    assert not database.exists()


def test_operations_fail_legibly_before_ensure_schema():
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=":memory:"))

    with pytest.raises(sqlite3.OperationalError):
        store.create_workspace("chad", "scratch")


def _seed_unit(store, title: str, narrative: str, state: str) -> None:
    # Direct INSERT is test plumbing: unit CREATION is Phase 6's
    # Package flow; Phase 3 only proves the search mechanism.
    with store._connection as connection:
        connection.execute(
            "INSERT INTO unit_of_work (workspace_id, title, narrative, "
            "source_turn_refs, state, author, created_at, updated_at) "
            "VALUES (1, ?, ?, '[]', ?, 'dana', 't', 't')",
            (title, narrative, state),
        )


def test_search_published_units_on_an_empty_library_returns_nothing():
    # No ensure_schema first: the search is the one read that must
    # work against a store nothing has written to yet.
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=":memory:"))
    assert store.search_published_units("flag rate") == []


def test_search_published_units_finds_seeded_rows_by_state(store):
    _seed_unit(store, "Flag rates by supplier", "RVX01 dominates.", "published")
    _seed_unit(store, "Draft thoughts", "flag rate musings", "draft")
    _seed_unit(store, "Canonical flag rate primer", "The one truth.", "canonical")

    matches = store.search_published_units("flag rate")

    assert [(m.title, m.state) for m in matches] == [
        ("Flag rates by supplier", "published"),
        ("Canonical flag rate primer", "canonical"),
    ]
    assert matches[0].author == "dana"
    assert matches[0].snippet.startswith("RVX01")


def test_search_matches_narrative_text_too(store):
    _seed_unit(store, "Supplier story", "The flag rate spiked in March.", "published")
    assert len(store.search_published_units("flag rate")) == 1
    assert store.search_published_units("nonexistent phrase") == []


def test_one_store_serves_reads_and_writes_from_two_threads(store):
    """The web layer writes turn_log from a worker thread while request
    threads read (Phase 5): one shared connection, serialized."""
    import threading

    workspace = store.create_workspace("chad", "scratch")
    conversation = store.create_conversation(workspace.id, "t")
    errors: list[BaseException] = []

    def writer():
        try:
            for turn in range(1, 21):
                store.append_turn_log(
                    TurnLogEntry(
                        conversation_id=conversation.id,
                        turn=turn,
                        actor="chad",
                        action="ask",
                        created_at=datetime.now(UTC),
                    )
                )
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    def reader():
        try:
            for _ in range(20):
                store.list_conversations(workspace.id)
                store.list_turn_logs(conversation.id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(store.list_turn_logs(conversation.id)) == 20


# --- Phase 5 Block 3: workspace/conversation CRUD and the migration ------


def test_get_workspace_and_delete_only_when_empty(store):
    workspace = store.create_workspace("chad", "audit")
    assert store.get_workspace(workspace.id) == workspace
    assert store.get_workspace(9999) is None

    conversation = store.create_conversation(workspace.id, "t")
    with pytest.raises(WorkspaceNotEmptyError, match="1 conversation"):
        store.delete_workspace(workspace.id)
    assert store.get_workspace(workspace.id) is not None

    store.delete_conversation(conversation.id)
    store.delete_workspace(workspace.id)
    assert store.get_workspace(workspace.id) is None
    store.delete_workspace(workspace.id)  # missing: a no-op, not an error


def test_rename_conversation(store):
    workspace = store.create_workspace("chad", "scratch")
    conversation = store.create_conversation(workspace.id, "first words")
    renamed = store.rename_conversation(conversation.id, "Flag rates by supplier")
    assert renamed.title == "Flag rates by supplier"
    assert renamed.id == conversation.id
    assert renamed.created_at == conversation.created_at
    assert store.get_conversation(conversation.id).title == "Flag rates by supplier"
    assert store.rename_conversation(9999, "x") is None


def test_delete_conversation_cascades_turn_log_and_unshared_bundles(store):
    """The rows go; a bundle another conversation also cites stays,
    because refs are content-addressed and may be shared."""
    workspace = store.create_workspace("chad", "scratch")
    doomed = store.create_conversation(workspace.id, "doomed")
    keeper = store.create_conversation(workspace.id, "keeper")
    store.save_evidence_bundle("only-doomed", "{}")
    store.save_evidence_bundle("shared", "{}")
    store.append_turn_log(_entry(doomed.id, 1, evidence_bundle_ref="only-doomed"))
    store.append_turn_log(_entry(doomed.id, 2, evidence_bundle_ref="shared"))
    store.append_turn_log(_entry(keeper.id, 1, evidence_bundle_ref="shared"))

    store.delete_conversation(doomed.id)

    assert store.get_conversation(doomed.id) is None
    assert store.list_turn_logs(doomed.id) == []
    assert store.load_evidence_bundle("only-doomed") is None
    assert store.load_evidence_bundle("shared") == "{}"
    assert [c.id for c in store.list_conversations(workspace.id)] == [keeper.id]
    assert len(store.list_turn_logs(keeper.id)) == 1
    store.delete_conversation(9999)  # missing: a no-op


def test_delete_conversation_drops_its_checkpoint_thread(tmp_path):
    database = tmp_path / "work.db"
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=str(database)))
    store.ensure_schema()
    workspace = store.create_workspace("chad", "scratch")
    conversation = store.create_conversation(workspace.id, "t")
    other = store.create_conversation(workspace.id, "other")

    saver = store.checkpointer()
    checkpoint = {
        "v": 4, "id": "chk-1", "ts": "2026-05-30T00:00:00+00:00",
        "channel_values": {"turn": 1}, "channel_versions": {}, "versions_seen": {},
    }
    for thread in (conversation.id, other.id):
        config = {"configurable": {"thread_id": str(thread), "checkpoint_ns": ""}}
        saver.put(config, checkpoint, {}, {})

    store.delete_conversation(conversation.id)

    reread = store.checkpointer()
    gone = {"configurable": {"thread_id": str(conversation.id), "checkpoint_ns": ""}}
    kept = {"configurable": {"thread_id": str(other.id), "checkpoint_ns": ""}}
    assert reread.get(gone) is None
    assert reread.get(kept) is not None


# The turn_log DDL as it stood before Block 3 (no question/outcome).
_OLD_TURN_LOG = """
CREATE TABLE workspace (id INTEGER PRIMARY KEY, owner TEXT NOT NULL,
    name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE conversation (id INTEGER PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspace(id),
    title TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE turn_log (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    turn INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    tools_used TEXT, substrates_read TEXT, evidence_bundle_ref TEXT,
    verifier_verdict TEXT, substrate_versions TEXT, status_events TEXT,
    created_at TEXT NOT NULL);
INSERT INTO workspace VALUES (1, 'chad', 'scratch', '2026-08-31T00:00:00+00:00');
INSERT INTO conversation VALUES (3, 1, 'old thread', '2026-08-31T00:00:00+00:00');
INSERT INTO turn_log (conversation_id, turn, actor, action, tools_used,
    substrates_read, evidence_bundle_ref, verifier_verdict,
    substrate_versions, status_events, created_at)
  VALUES (3, 1, 'chad', 'ask', '["run_sql"]', '[]', NULL, NULL, '[]',
          '[]', '2026-08-31T00:00:01+00:00');
"""


def test_ensure_schema_migrates_an_older_store_in_place(tmp_path):
    """A work.db written before Block 3 gains the two columns and keeps
    its rows; old rows read back with an empty question and no
    outcome, new rows carry both."""
    database = tmp_path / "work.db"
    with sqlite3.connect(str(database)) as connection:
        connection.executescript(_OLD_TURN_LOG)
    store = SqliteWorkStore(SqliteWorkStoreSettings(database=str(database)))

    store.ensure_schema()
    store.ensure_schema()  # idempotent after the migration too

    columns = [
        row[1]
        for row in store._connection.execute("PRAGMA table_info(turn_log)").fetchall()
    ]
    assert "question" in columns and "outcome" in columns
    (old,) = store.list_turn_logs(3)
    assert old.turn == 1 and old.tools_used == ["run_sql"]
    assert old.question == "" and old.outcome is None

    store.append_turn_log(_entry(3, 2))
    assert [e.question for e in store.list_turn_logs(3)] == ["", "how many?"]
    assert store.list_turn_logs(3)[1].outcome == '{"kind": "refuse", "reason": "r"}'
