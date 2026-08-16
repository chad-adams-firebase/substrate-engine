"""Declarative base and shared column types for the data model.

Monetary amounts and rates are stored as ``Float``: SQLite has no native
decimal type, values are synthetic, and float round-trips are
deterministic — which the bit-identical simulation reruns depend on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAIVE_TIMESTAMP_ERROR = (
    "Timestamps must be aware UTC datetimes from the injected clock"
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every InvoiceGuard table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator):
    """Aware-UTC datetime column backed by SQLite's naive storage.

    Binding rejects naive datetimes — the database-boundary enforcement of
    the clock law — and normalizes aware input to UTC before storing it
    naive, so lexical order equals chronological order. Loaded values come
    back aware UTC. Query bind parameters flow through the same path, so
    naive comparison operands are rejected too.

    Raw-SQL call sites (the spec's declared stale-sweep and dashboard
    queries) must render comparison values the same way:
    ``clock.now().astimezone(timezone.utc).replace(tzinfo=None)``.
    """

    impl = DateTime
    cache_ok = True

    @property
    def python_type(self):
        """Values bind and load as ``datetime`` (aware UTC on load)."""
        return datetime

    def process_bind_param(self, value, dialect):
        """Store an aware datetime as naive UTC; reject naive input."""
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(NAIVE_TIMESTAMP_ERROR)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        """Return the stored naive-UTC value as an aware UTC datetime."""
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utc_naive(moment: datetime) -> datetime:
    """Render an aware datetime the way ``UTCDateTime`` stores it.

    For the spec's declared raw-SQL spots, which compare against stored
    timestamps without the ORM type in the middle.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(NAIVE_TIMESTAMP_ERROR)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)
