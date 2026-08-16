"""Findings and auditor feedback on them (spec §3).

Findings are append-mostly: an auditor never mutates or deletes a
finding — feedback zeroes its contribution through the feedback record.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from invoiceguard.clock import Clock
from invoiceguard.models.base import Base, UTCDateTime


class FindingCategory(enum.StrEnum):
    """Finding categories (spec §3). Name equals stored value."""

    MATERIAL = "MATERIAL"
    SERVICE = "SERVICE"
    FEE = "FEE"
    COMPLIANCE = "COMPLIANCE"
    CORRECTION = "CORRECTION"
    CREEPBACK = "CREEPBACK"
    NOTE = "NOTE"


class Finding(Base):
    """An issue a rule (or external report) raised against an invoice."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    line_number: Mapped[int | None] = mapped_column(Integer)
    rule_name: Mapped[str] = mapped_column(String(100))
    category: Mapped[FindingCategory] = mapped_column(
        Enum(FindingCategory, native_enum=False, validate_strings=True, length=15)
    )
    description: Mapped[str | None] = mapped_column(String(1000))
    amount: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    feedback: Mapped[FindingFeedback | None] = relationship(
        back_populates="finding", uselist=False
    )

    def effective_amount(self) -> float:
        """Dollars this finding contributes to roll-up (spec §6).

        Zero when an auditor's feedback marks it a valid exception;
        otherwise the finding's amount. Flat/flag findings without an
        amount contribute zero.
        """
        if self.feedback is not None and self.feedback.valid_exception:
            return 0.0
        if self.amount is None:
            return 0.0
        return self.amount


def persist_finding(
    session: Session,
    clock: Clock,
    *,
    invoice_id: int,
    rule_name: str,
    category: FindingCategory,
    line_number: int | None = None,
    description: str | None = None,
    amount: float | None = None,
) -> Finding | None:
    """Create a finding unless a matching one already exists (spec §3).

    The one idempotency seam every findings producer uses — line mapping's
    note findings now, the rules engine, compliance intake, and creepback
    detection later. A finding matches on ``(invoice_id, line_number,
    rule_name)``; when a match exists nothing is written and ``None`` is
    returned, so re-running a stage never duplicates its findings.

    ``line_number`` may be None for invoice-level findings; the lookup
    uses an IS NULL comparison so those dedupe too. Never flushes or
    commits — the calling stage owns the transaction (autoflush makes
    two calls within one transaction see each other).
    """
    line_filter = (
        Finding.line_number.is_(None)
        if line_number is None
        else Finding.line_number == line_number
    )
    existing = session.execute(
        select(Finding)
        .where(Finding.invoice_id == invoice_id)
        .where(line_filter)
        .where(Finding.rule_name == rule_name)
    ).scalars().first()
    if existing is not None:
        return None
    finding = Finding(
        invoice_id=invoice_id,
        line_number=line_number,
        rule_name=rule_name,
        category=category,
        description=description,
        amount=amount,
        created_at=clock.now(),
    )
    session.add(finding)
    return finding


class FindingFeedback(Base):
    """An auditor's judgement on one finding.

    ``finding_id`` is unique: a finding carries at most one feedback
    record, and a second auditor's feedback replaces the first (replace
    semantics live in the feedback endpoint).

    ``cloned`` marks feedback carried forward from the matching finding
    of a prior revision (spec Act 4): the judgement is the original
    auditor's, restated on the new revision so the same issue is never
    re-judged.
    """

    __tablename__ = "finding_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), unique=True)
    auditor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    valid_exception: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_misfire: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_text: Mapped[str | None] = mapped_column(String(1000))
    misfire_text: Mapped[str | None] = mapped_column(String(1000))
    cloned: Mapped[bool] = mapped_column(Boolean, default=False)

    finding: Mapped[Finding] = relationship(back_populates="feedback")
