"""Auditor users and roles (``ig.platform.users`` data, spec §3)."""

from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from invoiceguard.models.base import Base


class Role(enum.StrEnum):
    """Roles gating API endpoints; resolved from the X-User header stub."""

    ADMIN = "admin"
    AUDITOR = "auditor"
    AUDIT_MANAGER = "audit_manager"


class User(Base):
    """An auditor (or admin) known to InvoiceGuard."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_name: Mapped[str] = mapped_column(String(50))
    team: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            native_enum=False,
            validate_strings=True,
            length=20,
            values_callable=lambda members: [m.value for m in members],
        )
    )
    available: Mapped[bool] = mapped_column(Boolean, default=True)
