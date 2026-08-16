"""The scheduled_tasks table (spec §3, §7).

All delayed behavior flows through rows in this table. The tick executor
that runs due tasks is the component ``ig.platform.scheduler`` and is not
part of the foundation layer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from invoiceguard.models.base import Base, UTCDateTime

# Task-name protocol identifiers shared by the components that arm a task
# and the executor registry that dispatches it. They live on the table
# module — a leaf every component already imports — so an armer never has
# to import the module that owns the handler (which would tie the pipeline
# dispatch chain into import cycles). Thresholds (delays, expiries) stay
# with the components that apply them.
HOLD_RECHECK_TASK = "hold_recheck"
SKIP_COMPLIANCE_TASK = "skip_compliance"
CREEPBACK_SCAN_TASK = "creepback_scan"
NIGHTLY_RECALC_TASK = "nightly_recalc"
STALE_SWEEP_TASK = "stale_sweep"


class ScheduledTask(Base):
    """One unit of delayed work, executed once when due."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(100))
    args_json: Mapped[dict | None] = mapped_column(JSON)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
