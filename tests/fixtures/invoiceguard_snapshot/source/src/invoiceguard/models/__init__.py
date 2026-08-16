"""SQLAlchemy data model for every InvoiceGuard table (spec §3).

One module per table cluster; this package is the single import point for
``Base`` and all mapped classes, so ``Base.metadata.create_all`` sees the
full schema.
"""

from invoiceguard.models.base import Base, UTCDateTime, utc_naive
from invoiceguard.models.config import ConfigRow
from invoiceguard.models.finding import (
    Finding,
    FindingCategory,
    FindingFeedback,
    persist_finding,
)
from invoiceguard.models.invoice import (
    TERMINAL_STATUSES,
    Invoice,
    TerminalStatusError,
    InvoiceHistory,
    InvoiceLine,
    InvoiceStatus,
    LineType,
    SupplierAcceptance,
)
from invoiceguard.models.report import (
    ComplianceReport,
    ComplianceRule,
    ReviewReport,
    ReviewReportLine,
)
from invoiceguard.models.scheduled_task import (
    CREEPBACK_SCAN_TASK,
    HOLD_RECHECK_TASK,
    NIGHTLY_RECALC_TASK,
    SKIP_COMPLIANCE_TASK,
    STALE_SWEEP_TASK,
    ScheduledTask,
)
from invoiceguard.models.supplier import Contract, Supplier
from invoiceguard.models.user import Role, User

__all__ = [
    "Base",
    "UTCDateTime",
    "utc_naive",
    "ConfigRow",
    "Finding",
    "FindingCategory",
    "FindingFeedback",
    "persist_finding",
    "TERMINAL_STATUSES",
    "Invoice",
    "TerminalStatusError",
    "InvoiceHistory",
    "InvoiceLine",
    "InvoiceStatus",
    "LineType",
    "SupplierAcceptance",
    "ComplianceReport",
    "ComplianceRule",
    "ReviewReport",
    "ReviewReportLine",
    "ScheduledTask",
    "CREEPBACK_SCAN_TASK",
    "HOLD_RECHECK_TASK",
    "NIGHTLY_RECALC_TASK",
    "SKIP_COMPLIANCE_TASK",
    "STALE_SWEEP_TASK",
    "Contract",
    "Supplier",
    "Role",
    "User",
]
