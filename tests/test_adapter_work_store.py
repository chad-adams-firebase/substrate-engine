"""SQLite WorkStore adapter: schema bootstrap is idempotent, workspace
and conversation CRUD round-trip, turn logs and evidence bundles
persist, and the checkpointer stays lazy."""

import sqlite3
from datetime import UTC, datetime

import pytest

from engine.adapters.work_store_sqlite import (
    SqliteWorkStore,
    SqliteWorkStoreSettings,
)
from engine.ports.types import TurnLogEntry


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
