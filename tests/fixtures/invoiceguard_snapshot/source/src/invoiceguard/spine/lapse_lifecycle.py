"""Stale sweep: lapse invoices that sat unworked too long.

The component ``ig.spine.lapse-lifecycle`` (spec Act 5). A daily
``stale_sweep`` scheduled task flips still-pending invoices older than
the cutoff to ``LAPSED``. The cutoff is ``lapse_after_days`` (config)
plus ``LAPSE_GRACE_DAYS`` of hardcoded grace, measured back from the
injected clock.

The sweep deliberately protects: any status outside the sweep-lapsable
set (terminals are never touched), rows missing a received timestamp,
and invoices whose denormalized ``invoices.compliance_score`` is at or
above ``COMPLIANCE_SCORE_CRITICAL``. Per the spec amendments, that
column is Act 5's protection signal — intentionally distinct from
roll-up's Σ COMPLIANCE finding amounts, which an auditor's exception
can zero; sweep protection survives such feedback.

The candidate query is the spec's first declared raw-SQL spot. Its
timestamp comparison must render values the way ``UTCDateTime`` stores
them — naive UTC, via ``utc_naive`` — and everything after the id
select stays ORM so every lapse flows through the one transition
helper and writes history.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from invoiceguard.clock import Clock
from invoiceguard.models import Invoice, InvoiceStatus, utc_naive
from invoiceguard.platform.bootstrap.logging_setup import format_event
from invoiceguard.platform.config import ConfigService
from invoiceguard.spine.rollup import COMPLIANCE_SCORE_CRITICAL

logger = logging.getLogger("invoiceguard.lapse_lifecycle")

ACTOR = "system.stale-sweep"
LAPSE_GRACE_DAYS = 1

# The only statuses the sweep may lapse (spec §4); bound as parameters so
# the raw SQL and this named constant can never disagree.
SWEEP_LAPSABLE_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {InvoiceStatus.READY, InvoiceStatus.RECEIVED}
)

# Raw-SQL spot 1 of 2 (spec §3): the stale-sweep candidate query.
STALE_CANDIDATES_SQL = text(
    """
    SELECT id FROM invoices
    WHERE status IN (:ready, :received)
      AND received_at IS NOT NULL
      AND received_at < :cutoff
      AND (compliance_score IS NULL OR compliance_score < :critical)
    ORDER BY id
    """
)


def run_stale_sweep(session: Session, clock: Clock, args: dict) -> int:
    """Scheduled-task handler for ``stale_sweep`` (spec §7, Act 5).

    Selects the sweepable candidates in one raw-SQL pass, then lapses
    each through the transition helper (actor ``system.stale-sweep``)
    so history rows are written. Emits one summary event and returns
    the number of invoices lapsed (the tick executor ignores it; the
    CLI wrapper prints it). ``args`` is unused — the sweep always
    covers the whole backlog. Never commits; the tick executor (or the
    CLI wrapper) owns the transaction.
    """
    config = ConfigService(session, clock).current()
    cutoff = utc_naive(clock.now()) - timedelta(
        days=config.lapse_after_days + LAPSE_GRACE_DAYS
    )
    rows = session.execute(
        STALE_CANDIDATES_SQL,
        {
            "ready": InvoiceStatus.READY.value,
            "received": InvoiceStatus.RECEIVED.value,
            # Bound as the exact string SQLite stores (naive UTC with
            # microseconds) so the comparison is the same lexical order
            # the ORM type relies on.
            "cutoff": cutoff.isoformat(sep=" ", timespec="microseconds"),
            "critical": COMPLIANCE_SCORE_CRITICAL,
        },
    ).all()
    candidate_ids = [row.id for row in rows]

    for invoice_id in candidate_ids:
        invoice = session.get(Invoice, invoice_id)
        invoice.transition_to(
            InvoiceStatus.LAPSED, actor=ACTOR, clock=clock, session=session
        )

    logger.info(
        format_event(
            "stale_sweep_completed",
            candidates=len(candidate_ids),
            cutoff=cutoff.isoformat(),
            lapsed=len(candidate_ids),
        )
    )
    return len(candidate_ids)
