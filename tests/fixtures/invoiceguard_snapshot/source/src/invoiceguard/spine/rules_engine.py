"""The rules engine: twelve audit rules over every mapped invoice.

The component ``ig.spine.rules-engine`` (spec §5, Act 2). Line mapping
dispatches here once per mapped invoice; each rule is its own function
with a named-constant tripping condition, and every finding goes through
the idempotent ``persist_finding`` seam. Tunable thresholds come from
the versioned config table; fixed thresholds are the module constants
below. When the walk finishes, the engine dispatches onward to
prior-audit compliance.

Finding categories follow the table in ``RULE_CATEGORIES``: line-scoped
dollar rules inherit their line's class (MATERIAL/SERVICE/FEE) so the
roll-up sums attribute dollars to the right bucket; the two amount-zero
informational flags are NOTE findings, which the roll-up excludes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from invoiceguard.clock import Clock
from invoiceguard.models import (
    Contract,
    Finding,
    FindingCategory,
    Invoice,
    InvoiceLine,
    LineType,
    Supplier,
    persist_finding,
)
from invoiceguard.platform.bootstrap.logging_setup import format_event
from invoiceguard.platform.config import ConfigService
from invoiceguard.spine import prior_audit_compliance

logger = logging.getLogger("invoiceguard.rules_engine")

# Fixed thresholds (spec §5); the "(c)" thresholds live in config.
QUANTITY_SPIKE_FACTOR = 3.0
DUPLICATE_WINDOW_DAYS = 30
NEW_SUPPLIER_DAYS = 365
FREIGHT_PCT_CAP = 0.08
# How a rush-fee line is recognized: a FEE line whose description
# mentions rush (case-insensitive). A generator contract — the spec
# names the rule but not the marker.
RUSH_FEE_KEYWORD = "rush"
# The spec's total_mismatch condition is a literal "≠"; a cent of
# tolerance absorbs float noise without hiding any real mismatch.
TOTAL_MISMATCH_TOLERANCE = 0.01

# The category each rule's findings carry. LINE_CLASS means the finding
# inherits its line's MATERIAL/SERVICE/FEE class; the roll-up formula
# (spec §6) sums by these categories.
LINE_CLASS = "line_class"
RULE_CATEGORIES: dict[str, object] = {
    "rate_variance": LINE_CLASS,
    "unapproved_item": LINE_CLASS,
    "quantity_spike": LINE_CLASS,
    "duplicate_line": LINE_CLASS,
    "new_supplier": FindingCategory.NOTE,
    "freight_overcharge": FindingCategory.FEE,
    "rush_fee_unjustified": FindingCategory.FEE,
    "markup_over_list": LINE_CLASS,
    "service_hours_excessive": FindingCategory.SERVICE,
    "contract_lapsed_rate": LINE_CLASS,
    "total_mismatch": FindingCategory.FEE,
    "split_billing": FindingCategory.NOTE,
}

_EPOCH_FLOOR = datetime.min.replace(tzinfo=timezone.utc)


def run_rules(session: Session, clock: Clock, invoice: Invoice) -> None:
    """Apply every audit rule to one mapped invoice, then dispatch on.

    Walks lines in line-number order, then the invoice-level rules, so
    findings and logs are order-stable across identical runs. Re-runs
    add nothing: every write goes through ``persist_finding``.
    """
    config = ConfigService(session, clock).current()
    lines = sorted(invoice.lines, key=lambda line: line.line_number)
    contracts = _supplier_contracts(session, invoice.supplier_id)

    for line in lines:
        rule_rate_variance(session, clock, invoice, line, contracts, config)
        rule_unapproved_item(session, clock, invoice, line, contracts)
        rule_quantity_spike(session, clock, invoice, line)
        rule_duplicate_line(session, clock, invoice, line, lines)
        rule_rush_fee_unjustified(session, clock, invoice, line)
        rule_markup_over_list(session, clock, invoice, line, contracts, config)
        rule_service_hours_excessive(session, clock, invoice, line, config)
        rule_contract_lapsed_rate(session, clock, invoice, line, contracts)

    rule_new_supplier(session, clock, invoice)
    rule_freight_overcharge(session, clock, invoice, lines)
    rule_total_mismatch(session, clock, invoice, lines)
    rule_split_billing(session, clock, invoice, config)

    finding_count = session.execute(
        select(Finding).where(Finding.invoice_id == invoice.id)
    ).scalars().all()
    logger.info(
        format_event(
            "rules_completed",
            finding_count=len(finding_count),
            invoice_id=invoice.id,
        )
    )
    prior_audit_compliance.run_prior_audit(session, clock, invoice)


def rule_rate_variance(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    contracts: list[Contract],
    config,
) -> None:
    """A line billed meaningfully above its contracted rate.

    Trips when the unit rate exceeds the active contract rate by more
    than ``rate_variance_pct`` (config-tunable). The recovery amount is
    the full delta back to contract, not just the part beyond the
    threshold.
    """
    rate_variance_pct = config.rate_variance_pct
    contract = _active_contract(contracts, line.item_code, invoice.received_at)
    if contract is None:
        return
    if line.unit_rate > contract.contract_rate * (1.0 + rate_variance_pct):
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="rate_variance",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Rate {line.unit_rate} exceeds contract rate "
                f"{contract.contract_rate} by more than "
                f"{rate_variance_pct:.0%}"
            ),
            amount=(line.unit_rate - contract.contract_rate) * line.quantity,
        )


def rule_unapproved_item(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    contracts: list[Contract],
) -> None:
    """A line for an item the supplier has no active contract for.

    Everything billed off-contract is recovery opportunity, so the
    amount is the full extended price.
    """
    if _active_contract(contracts, line.item_code, invoice.received_at) is None:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="unapproved_item",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Item {line.item_code} is not on the supplier's active "
                "contract"
            ),
            amount=line.extended_price,
        )


def rule_quantity_spike(
    session: Session, clock: Clock, invoice: Invoice, line: InvoiceLine
) -> None:
    """A quantity far above this supplier's history for the item.

    Trips when the quantity exceeds ``QUANTITY_SPIKE_FACTOR`` times the
    supplier's trailing average for the item — the mean line quantity
    across the supplier's *other* invoices (the invoice's own revision
    chain is excluded) received before this one. No history means no
    baseline and no trip. The excess is measured against the average
    itself: everything above normal usage is opportunity, not just the
    part beyond the trip threshold.
    """
    history = session.execute(
        select(InvoiceLine.quantity)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .where(Invoice.supplier_id == invoice.supplier_id)
        .where(Invoice.invoice_number != invoice.invoice_number)
        .where(Invoice.received_at < invoice.received_at)
        .where(InvoiceLine.item_code == line.item_code)
        .order_by(InvoiceLine.id)
    ).scalars().all()
    if not history:
        return
    trailing_average = sum(history) / len(history)
    if line.quantity > QUANTITY_SPIKE_FACTOR * trailing_average:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="quantity_spike",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Quantity {line.quantity} exceeds "
                f"{QUANTITY_SPIKE_FACTOR}x the trailing average "
                f"{trailing_average}"
            ),
            amount=(line.quantity - trailing_average) * line.unit_rate,
        )


def rule_duplicate_line(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    lines: list[InvoiceLine],
) -> None:
    """The same item, quantity, and rate billed twice.

    Trips on a twin within the invoice, or in another of the supplier's
    invoices received in the ``DUPLICATE_WINDOW_DAYS`` before this one.
    The invoice's own revision chain is excluded — a revision repeating
    its prior's lines is the normal case, not double billing. Detection
    is one-directional (this invoice against what came before), so of a
    cross-invoice pair only the later invoice trips.
    """
    twin_in_invoice = any(
        other.line_number != line.line_number
        and other.item_code == line.item_code
        and other.quantity == line.quantity
        and other.unit_rate == line.unit_rate
        for other in lines
    )
    window_start = invoice.received_at - timedelta(days=DUPLICATE_WINDOW_DAYS)
    twin_elsewhere = session.execute(
        select(InvoiceLine)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .where(Invoice.supplier_id == invoice.supplier_id)
        .where(Invoice.invoice_number != invoice.invoice_number)
        .where(Invoice.received_at <= invoice.received_at)
        .where(Invoice.received_at >= window_start)
        .where(InvoiceLine.item_code == line.item_code)
        .where(InvoiceLine.quantity == line.quantity)
        .where(InvoiceLine.unit_rate == line.unit_rate)
        .order_by(InvoiceLine.id)
    ).scalars().first()
    if twin_in_invoice or twin_elsewhere is not None:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="duplicate_line",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Item {line.item_code} at {line.unit_rate} x "
                f"{line.quantity} appears more than once within "
                f"{DUPLICATE_WINDOW_DAYS} days"
            ),
            amount=line.extended_price,
        )


def rule_new_supplier(session: Session, clock: Clock, invoice: Invoice) -> None:
    """A supplier still inside its first contract year gets a review flag.

    Flat informational finding (amount 0) when the supplier was first
    contracted within ``NEW_SUPPLIER_DAYS`` of this invoice. Suppliers
    without a recorded first-contract date have no measurable age and do
    not trip.
    """
    supplier = session.get(Supplier, invoice.supplier_id)
    if supplier.first_contracted_at is None:
        return
    age = invoice.received_at - supplier.first_contracted_at
    if age < timedelta(days=NEW_SUPPLIER_DAYS):
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="new_supplier",
            category=FindingCategory.NOTE,
            description=(
                f"Supplier first contracted within {NEW_SUPPLIER_DAYS} days"
            ),
            amount=0.0,
        )


def rule_freight_overcharge(
    session: Session, clock: Clock, invoice: Invoice, lines: list[InvoiceLine]
) -> None:
    """Fee lines out of proportion to the goods they ride on.

    Trips when the invoice's FEE total exceeds ``FREIGHT_PCT_CAP`` of
    its MATERIAL subtotal; one invoice-level FEE finding carries the
    excess. With no material on the invoice every fee dollar is excess.
    """
    fee_total = sum(
        line.extended_price for line in lines if line.line_type is LineType.FEE
    )
    material_total = sum(
        line.extended_price
        for line in lines
        if line.line_type is LineType.MATERIAL
    )
    allowed = FREIGHT_PCT_CAP * material_total
    if fee_total > allowed:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="freight_overcharge",
            category=FindingCategory.FEE,
            description=(
                f"Fee total {fee_total} exceeds {FREIGHT_PCT_CAP:.0%} of "
                f"the material subtotal {material_total}"
            ),
            amount=fee_total - allowed,
        )


def rule_rush_fee_unjustified(
    session: Session, clock: Clock, invoice: Invoice, line: InvoiceLine
) -> None:
    """A rush fee on an invoice that was never flagged rush.

    A rush-fee line is a FEE line whose description mentions
    ``RUSH_FEE_KEYWORD``; when the invoice's rush flag is false the
    whole fee is recovery.
    """
    if invoice.rush_flag or line.line_type is not LineType.FEE:
        return
    if line.description is None:
        return
    if RUSH_FEE_KEYWORD in line.description.lower():
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="rush_fee_unjustified",
            category=FindingCategory.FEE,
            line_number=line.line_number,
            description="Rush fee billed on an invoice not flagged rush",
            amount=line.extended_price,
        )


def rule_markup_over_list(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    contracts: list[Contract],
    config,
) -> None:
    """A rate above the allowed markup over the item's list price.

    Trips when the unit rate exceeds list price times
    ``list_markup_cap`` (config-tunable); the amount is the excess over
    that allowance. Contracts without a list price cannot be judged.
    """
    list_markup_cap = config.list_markup_cap
    contract = _active_contract(contracts, line.item_code, invoice.received_at)
    if contract is None or contract.list_price is None:
        return
    allowed_rate = contract.list_price * list_markup_cap
    if line.unit_rate > allowed_rate:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="markup_over_list",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Rate {line.unit_rate} exceeds list price "
                f"{contract.list_price} x {list_markup_cap} markup cap"
            ),
            amount=(line.unit_rate - allowed_rate) * line.quantity,
        )


def rule_service_hours_excessive(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    config,
) -> None:
    """A service line burning more hours per unit than its class allows.

    The cap is the config-tunable per-item-class mapping (the item class
    is the item code's prefix before the first dash; the required
    ``"default"`` key covers unlisted classes). Excess hours are
    dollarized at the line's own implied hourly rate — extended price
    over service hours — not the per-unit rate.
    """
    service_hours_cap = config.service_hours_cap
    if line.line_type is not LineType.SERVICE:
        return
    if not line.service_hours or not line.quantity:
        return
    item_class = line.item_code.split("-", 1)[0]
    cap = service_hours_cap.get(item_class, service_hours_cap["default"])
    if line.service_hours / line.quantity > cap:
        hourly_rate = line.extended_price / line.service_hours
        excess_hours = line.service_hours - cap * line.quantity
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="service_hours_excessive",
            category=FindingCategory.SERVICE,
            line_number=line.line_number,
            description=(
                f"{line.service_hours} service hours exceed the "
                f"{cap} hours-per-unit cap for class {item_class}"
            ),
            amount=excess_hours * hourly_rate,
        )


def rule_contract_lapsed_rate(
    session: Session,
    clock: Clock,
    invoice: Invoice,
    line: InvoiceLine,
    contracts: list[Contract],
) -> None:
    """A line still priced by an expired contract revision.

    Trips when the unit rate exactly matches an expired contract row's
    rate while the active contract prices the item lower — the supplier
    kept billing the old, higher rate. Exact float equality is safe
    here: matching rates come from the same decimal strings. The amount
    is the delta back to the active rate.
    """
    active = _active_contract(contracts, line.item_code, invoice.received_at)
    if active is None or line.unit_rate <= active.contract_rate:
        return
    expired_match = any(
        contract.item_code == line.item_code
        and contract.effective_to is not None
        and contract.effective_to < invoice.received_at
        and contract.contract_rate == line.unit_rate
        for contract in contracts
    )
    if expired_match:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="contract_lapsed_rate",
            category=_line_class(line),
            line_number=line.line_number,
            description=(
                f"Rate {line.unit_rate} matches an expired contract "
                f"revision; the active rate is {active.contract_rate}"
            ),
            amount=(line.unit_rate - active.contract_rate) * line.quantity,
        )


def rule_total_mismatch(
    session: Session, clock: Clock, invoice: Invoice, lines: list[InvoiceLine]
) -> None:
    """A stated total that disagrees with the lines, unexplained.

    Trips when the invoice total differs from the sum of extended
    prices by more than ``TOTAL_MISMATCH_TOLERANCE`` and the adjustment
    flag is false. A true adjustment flag makes the mismatch legitimate
    (spec §9's planted gotcha) — deliberately no finding.
    """
    if invoice.adjustment_flag or invoice.invoice_total is None:
        return
    line_total = sum(line.extended_price for line in lines)
    difference = abs(invoice.invoice_total - line_total)
    if difference > TOTAL_MISMATCH_TOLERANCE:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="total_mismatch",
            category=FindingCategory.FEE,
            description=(
                f"Invoice total {invoice.invoice_total} differs from the "
                f"line total {line_total} with no adjustment flag"
            ),
            amount=difference,
        )


def rule_split_billing(
    session: Session, clock: Clock, invoice: Invoice, config
) -> None:
    """Several same-day invoices quietly splitting one PO past approval.

    Trips when this invoice shares its PO reference with at least one
    other invoice number from the supplier on the same received day and
    the group's totals jointly exceed ``po_approval_threshold``
    (config-tunable). Revision chains count once, at their newest
    revision. Flat flag, amount 0.
    """
    po_approval_threshold = config.po_approval_threshold
    if invoice.po_reference is None:
        return
    day_start = invoice.received_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day_end = day_start + timedelta(days=1)
    same_po = session.execute(
        select(Invoice)
        .where(Invoice.supplier_id == invoice.supplier_id)
        .where(Invoice.po_reference == invoice.po_reference)
        .where(Invoice.received_at >= day_start)
        .where(Invoice.received_at < day_end)
        .order_by(Invoice.id)
    ).scalars().all()
    newest_per_number: dict[str, Invoice] = {}
    for candidate in same_po:
        current = newest_per_number.get(candidate.invoice_number)
        if current is None or candidate.revision > current.revision:
            newest_per_number[candidate.invoice_number] = candidate
    if len(newest_per_number) < 2:
        return
    joint_total = sum(
        newest.invoice_total or 0.0 for newest in newest_per_number.values()
    )
    if joint_total > po_approval_threshold:
        persist_finding(
            session,
            clock,
            invoice_id=invoice.id,
            rule_name="split_billing",
            category=FindingCategory.NOTE,
            description=(
                f"{len(newest_per_number)} same-day invoices on PO "
                f"{invoice.po_reference} jointly exceed the approval "
                f"threshold {po_approval_threshold}"
            ),
            amount=0.0,
        )


def _line_class(line: InvoiceLine) -> FindingCategory:
    """A line's own MATERIAL/SERVICE/FEE class as a finding category."""
    return FindingCategory(line.line_type.value)


def _supplier_contracts(session: Session, supplier_id: int) -> list[Contract]:
    """All of one supplier's contract rows, in a stable order."""
    return list(
        session.execute(
            select(Contract)
            .where(Contract.supplier_id == supplier_id)
            .order_by(Contract.id)
        ).scalars()
    )


def _active_contract(
    contracts: list[Contract], item_code: str, at: datetime
) -> Contract | None:
    """The contract row covering an item at an instant.

    Coverage: effective_from (unset means always) has passed and
    effective_to (unset means open-ended) has not. Ties resolve to the
    newest effective_from, then the highest row id, so the pick is
    deterministic when windows overlap.
    """
    candidates = [
        contract
        for contract in contracts
        if contract.item_code == item_code
        and (contract.effective_from is None or contract.effective_from <= at)
        and (contract.effective_to is None or contract.effective_to >= at)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda contract: (
            contract.effective_from or _EPOCH_FLOOR,
            contract.id,
        ),
    )
