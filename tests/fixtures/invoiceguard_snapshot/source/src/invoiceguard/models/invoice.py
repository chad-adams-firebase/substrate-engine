"""Invoices, their lines, the status lifecycle, and the history log (spec §3–§4)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from invoiceguard.clock import Clock
from invoiceguard.models.base import Base, UTCDateTime


class TerminalStatusError(Exception):
    """Raised when a transition is attempted from a terminal status."""


class InvoiceStatus(enum.StrEnum):
    """Invoice lifecycle states (spec §4). Name equals stored value."""

    RECEIVED = "RECEIVED"
    READY = "READY"
    CLAIMED = "CLAIMED"
    IN_REVIEW = "IN_REVIEW"
    CLOSED = "CLOSED"
    NO_REVIEW_NEEDED = "NO_REVIEW_NEEDED"
    LAPSED = "LAPSED"


TERMINAL_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {InvoiceStatus.CLOSED, InvoiceStatus.NO_REVIEW_NEEDED, InvoiceStatus.LAPSED}
)


class LineType(enum.StrEnum):
    """Classification of an invoice line."""

    MATERIAL = "MATERIAL"
    SERVICE = "SERVICE"
    FEE = "FEE"


class SupplierAcceptance(enum.StrEnum):
    """Supplier's recorded response to a prior audit review."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Invoice(Base):
    """One revision of one supplier invoice.

    Redelivery idempotency: ``(supplier_id, invoice_number, revision)`` is
    unique, so a redelivered file can never create a duplicate row.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("supplier_id", "invoice_number", "revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    invoice_number: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, validate_strings=True, length=20)
    )
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    invoice_total: Mapped[float | None] = mapped_column(Float)
    adjustment_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    rush_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    is_credit_memo: Mapped[bool] = mapped_column(Boolean, default=False)
    disputed_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    po_reference: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str | None] = mapped_column(String(10))
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scored_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    opportunity: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    compliance_score: Mapped[float | None] = mapped_column(Float)
    service_hours_delta: Mapped[float | None] = mapped_column(Float)
    alt_source_pct_delta: Mapped[float | None] = mapped_column(Float)
    claimed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    prior_revision_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    supplier_acceptance: Mapped[SupplierAcceptance | None] = mapped_column(
        Enum(
            SupplierAcceptance,
            native_enum=False,
            validate_strings=True,
            length=10,
        )
    )

    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice")
    history: Mapped[list[InvoiceHistory]] = relationship(
        back_populates="invoice", order_by="InvoiceHistory.id"
    )

    def transition_to(
        self,
        new_status: InvoiceStatus,
        *,
        actor: str,
        clock: Clock,
        session: Session,
        allow_reactivation: bool = False,
    ) -> InvoiceHistory:
        """Move this invoice to ``new_status`` and log it (spec §4).

        The single status-transition path: every status change, including
        birth (current status unset, logged with ``from_status`` NULL),
        goes through here so ``invoice_history`` is a complete audit
        trail. Transitions out of a terminal status raise
        ``TerminalStatusError`` before any mutation.

        ``allow_reactivation`` is the spec's single sanctioned terminal
        exception (§4, Act 4): honored solely for LAPSED → CLAIMED, it
        lets an authorized direct claim reactivate a lapsed invoice.
        Every other terminal transition raises regardless of the flag.
        The business precondition — no newer chain revision exists —
        belongs to the direct-claim path (``ig.spine.queue``), the only
        sanctioned caller.

        Never flushes or commits — the calling stage owns the
        transaction.
        """
        current = self.status
        if current in TERMINAL_STATUSES and not (
            allow_reactivation
            and current is InvoiceStatus.LAPSED
            and new_status is InvoiceStatus.CLAIMED
        ):
            raise TerminalStatusError(
                f"Invoice in terminal status {current} cannot move to {new_status}"
            )
        self.status = new_status
        entry = InvoiceHistory(
            invoice=self,
            from_status=current,
            to_status=new_status,
            actor=actor,
            at=clock.now(),
        )
        session.add(entry)
        return entry


class InvoiceLine(Base):
    """One line of an invoice, flattened from the parsed payload."""

    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    line_number: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit_rate: Mapped[float | None] = mapped_column(Float)
    extended_price: Mapped[float | None] = mapped_column(Float)
    line_type: Mapped[LineType | None] = mapped_column(
        Enum(LineType, native_enum=False, validate_strings=True, length=10)
    )
    service_hours: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(1000))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class InvoiceHistory(Base):
    """Audit log of every status transition (written only by the helper)."""

    __tablename__ = "invoice_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    from_status: Mapped[InvoiceStatus | None] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, validate_strings=True, length=20)
    )
    to_status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False, validate_strings=True, length=20)
    )
    actor: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(UTCDateTime)

    invoice: Mapped[Invoice] = relationship(back_populates="history")
