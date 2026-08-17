"""SQLite WorkStore adapter: schema bootstrap is idempotent and
workspace CRUD round-trips."""

import sqlite3

import pytest

from engine.adapters.work_store_sqlite import (
    SqliteWorkStore,
    SqliteWorkStoreSettings,
)


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

    assert {"workspace", "conversation", "unit_of_work", "comment", "turn_log"} <= tables


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
