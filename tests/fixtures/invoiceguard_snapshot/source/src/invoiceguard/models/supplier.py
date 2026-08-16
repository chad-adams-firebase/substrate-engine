"""Suppliers and their contracts (spec §3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from invoiceguard.models.base import Base, UTCDateTime


class Supplier(Base):
    """A contracted supplier submitting invoices.

    ``code`` is the stable business key that dropped invoice files carry
    (the XML ``supplier_code`` element); names are display-only and not
    unique, and database ids never appear in generated files.
    """

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    network: Mapped[bool] = mapped_column(Boolean)
    first_contracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Contract(Base):
    """One contracted item's pricing terms for a supplier."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    item_code: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    contract_rate: Mapped[float] = mapped_column(Float)
    list_price: Mapped[float | None] = mapped_column(Float)
    effective_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
