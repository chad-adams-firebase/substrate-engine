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
