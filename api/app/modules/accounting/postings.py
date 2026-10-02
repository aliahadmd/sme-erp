"""Transactional subscribers — the bridge from business events to the books.

Every handler runs on the PUBLISHER's session before it commits (see
`app.shared.events.dispatch_tx`), so a document and its journal entry are
saved atomically: if the entry cannot be posted, the whole request fails and
nothing is persisted. Handlers load the source document from that session
instead of trusting payload arithmetic, and voids mirror exactly what was
originally posted (`reverse_source_entries`).

Posting model (base currency throughout):
- AR invoice:  Dr AR total · Cr Sales revenue net · Cr Tax payable tax
- AP bill:     Dr GRNI (goods lines, net) · Dr Purchases (other lines + all
               tax — input tax is not recovered) · Cr AP total
- Credit notes mirror their invoice type from their own lines.
- Receipt:     Dr Inventory · Cr GRNI (perpetual inventory, at receipt cost)
- Delivery:    Dr COGS · Cr Inventory (moving-average cost)
- Adjustment:  net value between Inventory and Stock correction
- Payment:     money account vs AR/AP; AR/AP is cleared at each allocated
               invoice's snapshot rate and the difference to the payment-date
               rate posts to Realized FX Gain / Loss. Refunds of credit notes
               clear the customer/supplier credit balance on AR/AP.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.accounting.models import Account
from app.modules.accounting.service import (
    post_entry,
    resolve_account,
    resolve_payment_account,
    reverse_source_entries,
)
from app.shared.events import Event, subscribe_tx
from app.shared.money import money

logger = get_logger(__name__)

ZERO = Decimal("0")

Lines = list[tuple[Account, Decimal, Decimal]]


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


async def _fx_difference(
    session: AsyncSession, org_id: uuid.UUID, lines: Lines, difference: Decimal
) -> None:
    """Append the balancing FX line: positive difference = gain (credit)."""
    if difference > 0:
        lines.append((await resolve_account(session, org_id, "fx_gain"), ZERO, difference))
    elif difference < 0:
        lines.append((await resolve_account(session, org_id, "fx_loss"), -difference, ZERO))


# ------------------------------------------------------------------ invoices
async def _invoice_lines(session: AsyncSession, org_id: uuid.UUID, invoice) -> Lines:  # noqa: ANN001
    from app.modules.catalog.models import Product

    rate = Decimal(str(invoice.fx_rate))
    total = money(invoice.total_base)
    tax = money(Decimal(str(invoice.tax_total)) / rate)
    net = total - tax  # rounding residual stays in the net line
    is_ar = invoice.invoice_type.startswith("ar")
    is_credit = invoice.invoice_type.endswith("_credit")
    lines: Lines = []
    if is_ar:
        ar = await resolve_account(session, org_id, "ar")
        revenue = await resolve_account(session, org_id, "sales_revenue")
        tax_account = await resolve_account(session, org_id, "tax_payable")
        if is_credit:
            lines += [(ar, ZERO, total), (revenue, net, ZERO), (tax_account, tax, ZERO)]
        else:
            lines += [(ar, total, ZERO), (revenue, ZERO, net), (tax_account, ZERO, tax)]
        return lines

    ap = await resolve_account(session, org_id, "ap")
    purchases = await resolve_account(session, org_id, "purchases")
    if is_credit:
        return [(ap, total, ZERO), (purchases, ZERO, total)]
    # Goods lines clear the GRNI accrual booked at receipt; everything else
    # (services, free-text lines, non-recovered input tax) is purchases expense.
    goods_net = ZERO
    for line in invoice.lines:
        if line.product_id is None:
            continue
        product = await session.get(Product, line.product_id)
        if product is not None and product.type == "goods" and product.track_inventory:
            goods_net += Decimal(str(line.line_subtotal))
    goods_base = min(money(goods_net / rate), total)
    lines.append((ap, ZERO, total))
    if goods_base:
        lines.append((await resolve_account(session, org_id, "grni"), goods_base, ZERO))
    lines.append((purchases, total - goods_base, ZERO))
    return lines


async def _on_invoice_posted(session: AsyncSession, event: Event) -> None:
    from app.modules.invoicing.models import Invoice

    org_id = event.org_id
    assert org_id is not None
    invoice = await session.get(Invoice, _uuid(event.payload["invoice_id"]))
    assert invoice is not None
    is_ar = invoice.invoice_type.startswith("ar")
    label = (
        "Credit note"
        if invoice.invoice_type.endswith("_credit")
        else ("AR invoice" if is_ar else "AP bill")
    )
    await post_entry(
        session,
        org_id,
        entry_date=invoice.invoice_date,
        memo=f"{label} {invoice.number}",
        source_type="ar_invoice" if is_ar else "ap_invoice",
        source_id=invoice.id,
        lines=await _invoice_lines(session, org_id, invoice),
    )


async def _on_invoice_voided(session: AsyncSession, event: Event) -> None:
    assert event.org_id is not None
    await reverse_source_entries(
        session,
        event.org_id,
        _uuid(event.payload["invoice_id"]),
        memo=f"Reversal: invoice {event.payload['number']}",
    )


# ------------------------------------------------------------------ payments
async def _payment_lines(session: AsyncSession, org_id: uuid.UUID, payment) -> Lines:  # noqa: ANN001
    from app.modules.invoicing.models import Invoice, PaymentAllocation

    money_account = await resolve_payment_account(session, org_id, payment.method)
    amount_base = money(payment.amount_base)
    pay_rate = Decimal(str(payment.fx_rate))
    incoming = payment.direction == "in"
    ar = await resolve_account(session, org_id, "ar")
    ap = await resolve_account(session, org_id, "ap")

    if payment.credit_note_id:
        # Refund of a credit note: clears the party's credit balance at the
        # credit note's snapshot rate; any rate difference is realized FX.
        credit_note = await session.get(Invoice, payment.credit_note_id)
        cleared = money(Decimal(str(payment.amount)) / Decimal(str(credit_note.fx_rate)))
        lines: Lines = []
        if incoming:  # supplier refunds us (AP credit note)
            lines += [(money_account, amount_base, ZERO), (ap, ZERO, cleared)]
            await _fx_difference(session, org_id, lines, amount_base - cleared)
        else:
            lines += [(ar, cleared, ZERO), (money_account, ZERO, amount_base)]
            await _fx_difference(session, org_id, lines, cleared - amount_base)
        return lines

    allocations = (
        await session.scalars(
            select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
        )
    ).all()
    cleared = ZERO  # AR/AP cleared at each invoice's snapshot rate
    allocated = ZERO
    for allocation in allocations:
        invoice = await session.get(Invoice, allocation.invoice_id)
        amount = Decimal(str(allocation.amount))
        allocated += amount
        cleared += money(amount / Decimal(str(invoice.fx_rate)))
    unallocated = Decimal(str(payment.amount)) - allocated
    if unallocated > 0:
        cleared += money(unallocated / pay_rate)

    party = ar if incoming else ap
    lines: Lines = []
    if incoming:
        lines += [(money_account, amount_base, ZERO), (party, ZERO, cleared)]
        await _fx_difference(session, org_id, lines, amount_base - cleared)
    else:
        lines += [(party, cleared, ZERO), (money_account, ZERO, amount_base)]
        await _fx_difference(session, org_id, lines, cleared - amount_base)
    return lines


async def _on_payment_recorded(session: AsyncSession, event: Event) -> None:
    from app.modules.invoicing.models import Payment

    org_id = event.org_id
    assert org_id is not None
    payment = await session.get(Payment, _uuid(event.payload["payment_id"]))
    assert payment is not None
    await post_entry(
        session,
        org_id,
        entry_date=payment.payment_date,
        memo=f"Payment {payment.number}",
        source_type="payment",
        source_id=payment.id,
        lines=await _payment_lines(session, org_id, payment),
    )


async def _on_payment_allocated(session: AsyncSession, event: Event) -> None:
    """Later allocation of an unapplied payment amount: the amount was cleared
    at the payment-date rate; re-state it at the invoice's snapshot rate."""
    from app.modules.invoicing.models import Invoice, Payment

    org_id = event.org_id
    assert org_id is not None
    payment = await session.get(Payment, _uuid(event.payload["payment_id"]))
    invoice = await session.get(Invoice, _uuid(event.payload["invoice_id"]))
    amount = Decimal(str(event.payload["amount"]))
    at_payment_rate = money(amount / Decimal(str(payment.fx_rate)))
    at_invoice_rate = money(amount / Decimal(str(invoice.fx_rate)))
    delta = at_invoice_rate - at_payment_rate  # extra AR/AP to clear
    if delta == 0:
        return
    incoming = payment.direction == "in"
    party = await resolve_account(session, org_id, "ar" if incoming else "ap")
    lines: Lines = []
    if incoming:
        # clear more AR (credit) → loss; clear less AR (debit back) → gain
        lines.append((party, ZERO, delta) if delta > 0 else (party, -delta, ZERO))
        await _fx_difference(session, org_id, lines, -delta)
    else:
        lines.append((party, delta, ZERO) if delta > 0 else (party, ZERO, -delta))
        await _fx_difference(session, org_id, lines, delta)
    await post_entry(
        session,
        org_id,
        entry_date=date.today(),
        memo=f"FX on allocation of {payment.number} to {invoice.number}",
        source_type="payment",
        source_id=payment.id,
        lines=lines,
    )


async def _on_payment_voided(session: AsyncSession, event: Event) -> None:
    assert event.org_id is not None
    await reverse_source_entries(
        session,
        event.org_id,
        _uuid(event.payload["payment_id"]),
        memo=f"Reversal: payment {event.payload['number']}",
    )


# ----------------------------------------------------------------- inventory
async def _moves_value(session: AsyncSession, ref_type: str, ref_id: uuid.UUID) -> Decimal:
    from app.modules.inventory.models import StockMove

    moves = (
        await session.scalars(
            select(StockMove).where(StockMove.ref_type == ref_type, StockMove.ref_id == ref_id)
        )
    ).all()
    total = ZERO
    for move in moves:
        if move.qty < 0:
            total -= Decimal(str(move.cogs))
        else:
            total += Decimal(str(move.qty)) * Decimal(str(move.unit_cost))
    return money(total)


async def _on_receipt_posted(session: AsyncSession, event: Event) -> None:
    org_id = event.org_id
    assert org_id is not None
    receipt_id = _uuid(event.payload["receipt_id"])
    value = await _moves_value(session, "receipt", receipt_id)
    if value == 0:
        return
    await post_entry(
        session,
        org_id,
        entry_date=date.today(),
        memo=f"Goods received {event.payload['number']}",
        source_type="receipt",
        source_id=receipt_id,
        lines=[
            (await resolve_account(session, org_id, "inventory"), value, ZERO),
            (await resolve_account(session, org_id, "grni"), ZERO, value),
        ],
    )


async def _on_delivery_posted(session: AsyncSession, event: Event) -> None:
    org_id = event.org_id
    assert org_id is not None
    delivery_id = _uuid(event.payload["delivery_id"])
    cogs = -await _moves_value(session, "delivery", delivery_id)
    if cogs == 0:
        return
    await post_entry(
        session,
        org_id,
        entry_date=date.today(),
        memo=f"COGS for delivery {event.payload['number']}",
        source_type="delivery",
        source_id=delivery_id,
        lines=[
            (await resolve_account(session, org_id, "cogs"), cogs, ZERO),
            (await resolve_account(session, org_id, "inventory"), ZERO, cogs),
        ],
    )


async def _on_adjustment_posted(session: AsyncSession, event: Event) -> None:
    org_id = event.org_id
    assert org_id is not None
    adjustment_id = _uuid(event.payload["adjustment_id"])
    # Net VALUE decides the direction — lines may mix increases and decreases.
    value = await _moves_value(session, "adjustment", adjustment_id)
    if value == 0:
        return
    inventory = await resolve_account(session, org_id, "inventory")
    correction = await resolve_account(session, org_id, "stock_correction")
    if value > 0:
        lines = [(inventory, value, ZERO), (correction, ZERO, value)]
    else:
        lines = [(correction, -value, ZERO), (inventory, ZERO, -value)]
    await post_entry(
        session,
        org_id,
        entry_date=date.today(),
        memo=f"Stock adjustment {event.payload['number']}",
        source_type="adjustment",
        source_id=adjustment_id,
        lines=lines,
    )


async def _on_stock_voided(session: AsyncSession, event: Event) -> None:
    """Receipt / delivery void: mirror their journal entry."""
    assert event.org_id is not None
    await reverse_source_entries(
        session,
        event.org_id,
        _uuid(event.payload["document_id"]),
        memo=f"Reversal: {event.payload['number']}",
    )


_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    subscribe_tx("invoice.posted", _on_invoice_posted)
    subscribe_tx("invoice.voided", _on_invoice_voided)
    subscribe_tx("payment.recorded", _on_payment_recorded)
    subscribe_tx("payment.allocated", _on_payment_allocated)
    subscribe_tx("payment.voided", _on_payment_voided)
    subscribe_tx("receipt.posted", _on_receipt_posted)
    subscribe_tx("receipt.voided", _on_stock_voided)
    subscribe_tx("delivery.posted", _on_delivery_posted)
    subscribe_tx("delivery.voided", _on_stock_voided)
    subscribe_tx("adjustment.posted", _on_adjustment_posted)
    logger.info("accounting_subscribers_registered")
