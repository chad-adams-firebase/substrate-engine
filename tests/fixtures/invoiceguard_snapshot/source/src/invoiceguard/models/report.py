"""Prior-audit review reports and external compliance reports (spec §3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from invoiceguard.models.base import Base, UTCDateTime


class ReviewReport(Base):
    """An auditor's prior review of an invoice, delivered as *_review.json."""

    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    auditor_notes: Mapped[str | None] = mapped_column(String(2000))
    disposition: Mapped[str | None] = mapped_column(String(50))

    lines: Mapped[list[ReviewReportLine]] = relationship(
        back_populates="review_report", order_by="ReviewReportLine.id"
    )


class ReviewReportLine(Base):
    """One requested correction (new rate or removal) in a review report."""

    __tablename__ = "review_report_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_report_id: Mapped[int] = mapped_column(ForeignKey("review_reports.id"))
    line_number: Mapped[int] = mapped_column(Integer)
    requested_rate: Mapped[float | None] = mapped_column(Float)
    remove_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(1000))

    review_report: Mapped[ReviewReport] = relationship(back_populates="lines")


class ComplianceReport(Base):
    """An external contract-compliance report for one invoice."""

    __tablename__ = "compliance_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    total_score: Mapped[float] = mapped_column(Float)
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    rules: Mapped[list[ComplianceRule]] = relationship(
        back_populates="compliance_report", order_by="ComplianceRule.id"
    )


class ComplianceRule(Base):
    """One rule line item inside a compliance report."""

    __tablename__ = "compliance_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    compliance_report_id: Mapped[int] = mapped_column(
        ForeignKey("compliance_reports.id")
    )
    rule_code: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(1000))
    amount: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str | None] = mapped_column(String(20))

    compliance_report: Mapped[ComplianceReport] = relationship(
        back_populates="rules"
    )
