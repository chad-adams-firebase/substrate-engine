"""The versioned config table row (spec §3).

Only the ORM row lives here; retire-on-write semantics, the tunables
schema, and seeding belong to the component ``ig.platform.config``
(``invoiceguard.platform.config``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from invoiceguard.models.base import Base, UTCDateTime


class ConfigRow(Base):
    """One version of the tunable configuration.

    Rows are retired, never updated in place or deleted; the newest
    non-retired row (by autoincrement id) is the active configuration.
    """

    __tablename__ = "config"

    id: Mapped[int] = mapped_column(primary_key=True)
    values_json: Mapped[dict] = mapped_column(JSON)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
