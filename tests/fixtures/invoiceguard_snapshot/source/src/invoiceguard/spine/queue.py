"""Queue surfacing, GetNext, and the direct claim (spec Act 4).

The component ``ig.spine.queue``. Turns the scored backlog into each
auditor's prioritized queue and claims work through the one transition
helper.

The invoice-to-team ownership rule (implicit in the spec, stated here):
an invoice is eligible for an auditor if and only if it carries at least
one finding whose category is listed under the auditor's team in the
config ``team_to_categories`` mapping. An invoice whose findings all
belong to other teams — or an auditor whose team owns no categories —
surfaces nothing.

Eligibility beyond ownership: status ``READY``, unclaimed, supplier not
on the config exclusion list, and neither the credit-memo nor the
disputed-hold flag set. Ordering is by weight descending with
``PRIOR_AUDITOR_BOOST`` applied — as an ordering-time multiplier only,
never stored — when the auditor touched the invoice's revision chain
within the last ``SAVE_FOR_AUDITOR_BUSINESS_DAYS`` business day, then
by invoice id as the deterministic tiebreak.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from invoiceguard.clock import Clock
from invoiceguard.models import (
    Finding,
    FindingCategory,
    Invoice,
    InvoiceHistory,
    InvoiceStatus,
    Role,
    Supplier,
    User,
)
from invoiceguard.platform.bootstrap.logging_setup import format_event
from invoiceguard.platform.config import ConfigService, InvoiceGuardConfig

logger = logging.getLogger("invoiceguard.queue")

PRIOR_AUDITOR_BOOST = 5
SAVE_FOR_AUDITOR_BUSINESS_DAYS = 1
SATURDAY = 5  # datetime.weekday() value of the first weekend day

# Roles allowed to claim a specific invoice directly (spec Act 4).
DIRECT_CLAIM_ROLES: frozenset[Role] = frozenset(
    {Role.ADMIN, Role.AUDIT_MANAGER}
)

# Statuses a direct claim may start from: an ordinary claim of READY
# work, or the sanctioned reactivation of a LAPSED invoice.
DIRECT_CLAIM_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {InvoiceStatus.READY, InvoiceStatus.LAPSED}
)


class RoleNotPermittedError(PermissionError):
    """The caller's role does not allow a direct claim."""


class ClaimRefusedError(Exception):
    """A direct claim was refused; the invoice is unchanged."""


class NewerRevisionExistsError(ClaimRefusedError):
    """Reactivation refused: the LAPSED invoice has a newer revision."""


def business_days_ago(now: datetime, days: int) -> datetime:
    """The instant ``days`` business days before ``now``, weekends skipped.

    Steps back one calendar day per business day, then keeps stepping
    while the landing day is a Saturday or Sunday, preserving the time
    of day. Pure and clock-driven: ``now`` comes from the injected
    clock at every call site.
    """
    moment = now
    for _ in range(days):
        moment -= timedelta(days=1)
        while moment.weekday() >= SATURDAY:
            moment -= timedelta(days=1)
    return moment


def build_eligible_query(
    config: InvoiceGuardConfig, categories: list[FindingCategory]
) -> Select:
    """Invoices surfaceable to a team owning ``categories``.

    The eligibility rules shared by per-auditor queues and the
    queue-health dashboard: status READY, unclaimed, supplier not
    excluded, neither flag set, and at least one finding in an owned
    category.
    """
    ownership_exists = (
        select(Finding.id)
        .where(Finding.invoice_id == Invoice.id)
        .where(Finding.category.in_(categories))
        .exists()
    )
    return (
        select(Invoice)
        .join(Supplier, Invoice.supplier_id == Supplier.id)
        .where(Invoice.status == InvoiceStatus.READY)
        .where(Invoice.claimed_by.is_(None))
        .where(Supplier.code.not_in(config.excluded_supplier_codes))
        .where(Invoice.is_credit_memo.is_(False))
        .where(Invoice.disputed_hold.is_(False))
        .where(ownership_exists)
    )


def build_queue_query(
    session: Session, config: InvoiceGuardConfig, auditor: User
) -> Select:
    """The eligible-invoice query for one auditor (module docstring rules).

    Returns a ``Select`` of :class:`Invoice` rows; ranking happens in
    :func:`rank_queue` because the prior-auditor boost needs a
    revision-chain walk the query cannot express.
    """
    return build_eligible_query(config, _owned_categories(config, auditor))


def rank_queue(
    session: Session, clock: Clock, config: InvoiceGuardConfig, auditor: User
) -> list[Invoice]:
    """Eligible invoices in queue order for one auditor.

    Weight descending with the prior-auditor boost applied in memory —
    the stored weight is never modified — and invoice id ascending as
    the tiebreak.
    """
    if not _owned_categories(config, auditor):
        return []
    candidates = session.execute(
        build_queue_query(session, config, auditor)
    ).scalars().all()
    cutoff = business_days_ago(clock.now(), SAVE_FOR_AUDITOR_BUSINESS_DAYS)

    def sort_key(invoice: Invoice) -> tuple[float, int]:
        weight = invoice.weight or 0.0
        if _touched_chain_recently(session, invoice, auditor, cutoff):
            weight *= PRIOR_AUDITOR_BOOST
        return (-weight, invoice.id)

    return sorted(candidates, key=sort_key)


def get_next(session: Session, clock: Clock, auditor: User) -> Invoice | None:
    """Claim the top eligible invoice for ``auditor`` (spec Act 4).

    Sets ``claimed_by`` and transitions READY → CLAIMED with the
    auditor's short name as actor. An empty queue returns ``None`` —
    not an error. Never commits; the caller owns the transaction.
    """
    config = ConfigService(session, clock).current()
    ranked = rank_queue(session, clock, config, auditor)
    if not ranked:
        logger.info(format_event("queue_empty", auditor=auditor.short_name))
        return None
    invoice = ranked[0]
    invoice.claimed_by = auditor.id
    invoice.transition_to(
        InvoiceStatus.CLAIMED,
        actor=auditor.short_name,
        clock=clock,
        session=session,
    )
    logger.info(
        format_event(
            "queue_next_assigned",
            auditor=auditor.short_name,
            invoice_id=invoice.id,
            weight=invoice.weight,
        )
    )
    return invoice


def claim_specific(
    session: Session, clock: Clock, auditor: User, invoice_id: int
) -> Invoice:
    """Directly claim one invoice, reactivating LAPSED when sanctioned.

    Role-gated to audit managers and admins — enforced here as well as
    at the API layer, because the simulation driver calls this function
    without HTTP. A LAPSED invoice may be reactivated only when no newer
    revision exists in its chain (spec §4's single terminal exception,
    taken through the transition helper's ``allow_reactivation`` hatch);
    otherwise :class:`NewerRevisionExistsError` is raised. Any other
    non-READY status refuses with :class:`ClaimRefusedError`. Never
    commits; the caller owns the transaction.
    """
    if auditor.role not in DIRECT_CLAIM_ROLES:
        raise RoleNotPermittedError(
            f"Role {auditor.role} may not claim a specific invoice"
        )
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise LookupError(f"No invoice with id {invoice_id}")
    if invoice.status not in DIRECT_CLAIM_STATUSES:
        raise ClaimRefusedError(
            f"Invoice {invoice_id} in status {invoice.status} cannot be claimed"
        )

    reactivated = invoice.status is InvoiceStatus.LAPSED
    if reactivated and _newer_revision_exists(session, invoice):
        logger.warning(
            format_event(
                "claim_refused",
                auditor=auditor.short_name,
                invoice_id=invoice.id,
                reason="newer_revision_exists",
            )
        )
        raise NewerRevisionExistsError(
            f"Invoice {invoice_id} is superseded by a newer revision"
        )

    invoice.claimed_by = auditor.id
    invoice.transition_to(
        InvoiceStatus.CLAIMED,
        actor=auditor.short_name,
        clock=clock,
        session=session,
        allow_reactivation=reactivated,
    )
    logger.info(
        format_event(
            "invoice_claimed_directly",
            auditor=auditor.short_name,
            invoice_id=invoice.id,
            reactivated=reactivated,
        )
    )
    return invoice


def _owned_categories(
    config: InvoiceGuardConfig, auditor: User
) -> list[FindingCategory]:
    """The finding categories the auditor's team owns, as enum members."""
    names = config.team_to_categories.get(auditor.team or "", [])
    return [FindingCategory(name) for name in names]


def _revision_chain_ids(session: Session, invoice: Invoice) -> list[int]:
    """Ids of ``invoice`` and every prior revision in its chain."""
    ids = [invoice.id]
    current = invoice
    while current.prior_revision_id is not None:
        current = session.get(Invoice, current.prior_revision_id)
        ids.append(current.id)
    return ids


def _touched_chain_recently(
    session: Session, invoice: Invoice, auditor: User, cutoff: datetime
) -> bool:
    """Did ``auditor`` touch this revision chain since ``cutoff``?

    A touch is a history row the auditor wrote (actor = short name), or
    a chain member the auditor currently holds via ``claimed_by`` —
    inherited claims carry a system actor, so recency for those rides
    on the member's latest history row instead.
    """
    chain_ids = _revision_chain_ids(session, invoice)
    acted = session.execute(
        select(InvoiceHistory.id)
        .where(InvoiceHistory.invoice_id.in_(chain_ids))
        .where(InvoiceHistory.actor == auditor.short_name)
        .where(InvoiceHistory.at >= cutoff)
    ).first()
    if acted is not None:
        return True
    held = session.execute(
        select(InvoiceHistory.invoice_id)
        .join(Invoice, InvoiceHistory.invoice_id == Invoice.id)
        .where(InvoiceHistory.invoice_id.in_(chain_ids))
        .where(Invoice.claimed_by == auditor.id)
        .where(InvoiceHistory.at >= cutoff)
    ).first()
    return held is not None


def _newer_revision_exists(session: Session, invoice: Invoice) -> bool:
    """Is there a direct successor? Chains are linked lists, so one hop."""
    successor = session.execute(
        select(Invoice.id).where(Invoice.prior_revision_id == invoice.id)
    ).first()
    return successor is not None
