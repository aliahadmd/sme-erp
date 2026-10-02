"""Invoicing service — invoice lifecycle, credit notes, payments, allocations.

Settlement model:
- invoice open balance = total - amount_paid - applied_credits
- a posted credit note is applied to its original invoice's open balance
  first; whatever is left can be refunded in cash. On the credit note,
  `amount_paid` records the consumed amount (applied + refunded).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.service import get_setting
from app.modules.invoicing.models import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.shared.order_engine import apply_line_math, resolve_line_references
from app.shared.order_progress import remaining_by_line

ZERO = Decimal("0")
SETTLEABLE = ("posted", "partial", "paid")


def _d(value) -> Decimal:  # noqa: ANN001
    return Decimal(str(value or 0))


async def invoice_or_404(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID, *, for_update: bool = False
) -> Invoice:
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    if for_update:
        stmt = stmt.with_for_update()
    invoice = (await session.scalars(stmt)).first()
    if not invoice or invoice.org_id != org_id:
        raise NotFoundError("Invoice not found")
    return invoice


def is_credit(invoice: Invoice) -> bool:
    return invoice.invoice_type.endswith("_credit")


def open_balance(invoice: Invoice) -> Decimal:
    return _d(invoice.total) - _d(invoice.amount_paid) - _d(invoice.applied_credits)


def refresh_settlement_status(invoice: Invoice) -> None:
    """posted → partial → paid from the settled amount (never touches void/draft)."""
    if invoice.status in ("draft", "void"):
        return
    settled = _d(invoice.amount_paid) + _d(invoice.applied_credits)
    if settled >= _d(invoice.total):
        invoice.status = "paid"
    elif settled > 0:
        invoice.status = "partial"
    else:
        invoice.status = "posted"


# --------------------------------------------------------------------- lines
def _copy_line(source, position: int, qty) -> InvoiceLine:  # noqa: ANN001
    line = InvoiceLine(
        position=position,
        product_id=source.product_id,
        product_name=source.product_name,
        description=source.description,
        qty=qty,
        uom_code=source.uom_code,
        unit_price=source.unit_price,
        discount_pct=source.discount_pct,
        tax_id=source.tax_id,
        tax_rate_pct=source.tax_rate_pct,
    )
    apply_line_math(line)
    return line


def build_lines_from_invoice(original: Invoice) -> list[InvoiceLine]:
    """Copy snapshot lines from an invoice (for credit notes)."""
    return [_copy_line(line, position, line.qty) for position, line in enumerate(original.lines)]


def build_lines_from_order(order: object) -> list[InvoiceLine]:
    """Copy the not-yet-invoiced quantity of every order line (per line, so
    duplicate products and free-text lines are billed exactly once)."""
    remaining = remaining_by_line(order, "qty_invoiced")
    lines: list[InvoiceLine] = []
    for source in sorted(order.lines, key=lambda item: item.position):  # type: ignore[attr-defined]
        qty = remaining.get(source.id, ZERO)
        if qty <= 0:
            continue
        line = _copy_line(source, len(lines), qty)
        line.source_line_id = source.id
        lines.append(line)
    return lines


async def build_lines_from_input(
    session: AsyncSession, org_id: uuid.UUID, line_inputs: list, is_purchase: bool
) -> list[InvoiceLine]:
    lines: list[InvoiceLine] = []
    for position, line_in in enumerate(line_inputs):
        data = line_in.model_dump()
        data["_is_purchase"] = is_purchase
        data = await resolve_line_references(session, org_id, data)
        line = InvoiceLine(
            position=position,
            **{k: v for k, v in data.items() if not k.startswith("_")},
        )
        apply_line_math(line)
        lines.append(line)
    return lines


def order_amounts(
    invoice: Invoice,
) -> tuple[list[tuple[uuid.UUID, Decimal]], list[tuple[uuid.UUID, Decimal]]]:
    """(by order line, by product) quantities an order-sourced invoice bills."""
    by_line: dict[uuid.UUID, Decimal] = {}
    by_product: dict[uuid.UUID, Decimal] = {}
    for line in invoice.lines:
        if line.source_line_id is not None:
            by_line[line.source_line_id] = by_line.get(line.source_line_id, ZERO) + _d(line.qty)
        elif line.product_id is not None:
            by_product[line.product_id] = by_product.get(line.product_id, ZERO) + _d(line.qty)
    return list(by_line.items()), list(by_product.items())


# ---------------------------------------------------------------- lifecycle
async def post_invoice(
    session: AsyncSession,
    org_id: uuid.UUID,
    invoice: Invoice,
    actor_id: uuid.UUID,
) -> Invoice:
    if invoice.status != "draft":
        raise ConflictError(f"Cannot post an invoice in status '{invoice.status}'")
    if not invoice.lines:
        raise ValidationError("Cannot post an invoice without lines")
    if is_credit(invoice):
        await _apply_credit_note(session, org_id, invoice)
    is_ar = invoice.invoice_type.startswith("ar")
    entity = (
        ("ar_credit" if is_credit(invoice) else "ar_invoice")
        if is_ar
        else ("ap_credit" if is_credit(invoice) else "ap_invoice")
    )
    default_prefix = {
        "ar_invoice": "INV",
        "ap_invoice": "BILL",
        "ar_credit": "CRN",
        "ap_credit": "SCN",
    }[entity]
    prefixes = await get_setting(session, org_id, "numbering.prefixes")
    prefix = (
        prefixes.get(entity)
        if prefixes and isinstance(prefixes.get(entity), str)
        else default_prefix
    )
    from app.shared.numbering import next_number

    invoice.number = await next_number(session, org_id, entity, prefix)
    invoice.status = "posted"
    invoice.posted_at = datetime.now(UTC)
    invoice.posted_by = actor_id
    refresh_settlement_status(invoice)
    await session.flush()
    return invoice


async def _apply_credit_note(session: AsyncSession, org_id: uuid.UUID, credit: Invoice) -> None:
    """Enforce the credit cap at POST time (drafts are not binding) and apply
    the credit to the original invoice's open balance."""
    if credit.original_invoice_id is None:
        raise ValidationError("Credit notes must reference the original invoice")
    # Lock the original: concurrent credit postings must serialize.
    original = await invoice_or_404(session, org_id, credit.original_invoice_id, for_update=True)
    if original.status not in SETTLEABLE:
        raise ConflictError("Can only credit posted invoices")
    already = sum(
        (
            _d(c.total)
            for c in (
                await session.scalars(
                    select(Invoice).where(
                        Invoice.original_invoice_id == original.id,
                        Invoice.id != credit.id,
                        Invoice.status.in_(SETTLEABLE),
                    )
                )
            )
        ),
        ZERO,
    )
    if already + _d(credit.total) > _d(original.total):
        raise ConflictError(
            f"Credit exceeds the invoice total (already credited {already} of {original.total})"
        )
    applied = min(_d(credit.total), max(open_balance(original), ZERO))
    original.applied_credits = _d(original.applied_credits) + applied
    refresh_settlement_status(original)
    credit.amount_paid = applied  # consumed; the rest is refundable


async def refunded_amount(session: AsyncSession, credit: Invoice) -> Decimal:
    rows = await session.scalars(
        select(Payment.amount).where(
            Payment.credit_note_id == credit.id, Payment.status == "recorded"
        )
    )
    return sum((_d(a) for a in rows), ZERO)


async def void_invoice_settlement(
    session: AsyncSession, org_id: uuid.UUID, invoice: Invoice
) -> None:
    """Guards + settlement rollback for voiding a posted invoice/credit note."""
    if _d(invoice.applied_credits) > 0:
        raise ConflictError("Invoice has credit notes applied — void them first")
    if not is_credit(invoice):
        if _d(invoice.amount_paid) > 0:
            raise ConflictError("Invoice has payments allocated — void them first")
        return
    if await refunded_amount(session, invoice) > 0:
        raise ConflictError("Credit note has refunds recorded — void them first")
    applied = _d(invoice.amount_paid)
    if applied > 0 and invoice.original_invoice_id is not None:
        original = await invoice_or_404(
            session, org_id, invoice.original_invoice_id, for_update=True
        )
        original.applied_credits = max(_d(original.applied_credits) - applied, ZERO)
        refresh_settlement_status(original)
    invoice.amount_paid = ZERO


async def apply_allocation_to_invoice(invoice: Invoice, amount: Decimal, sign: int = 1) -> None:
    """Adjust paid amount and derived status. sign=-1 releases an allocation."""
    if invoice.status == "void" and sign > 0:
        raise ConflictError("Cannot allocate against a void invoice")
    invoice.amount_paid = _d(invoice.amount_paid) + sign * _d(amount)
    refresh_settlement_status(invoice)


# ------------------------------------------------------------------ payments
async def payment_or_404(
    session: AsyncSession, org_id: uuid.UUID, payment_id: uuid.UUID, *, for_update: bool = False
) -> Payment:
    stmt = select(Payment).where(Payment.id == payment_id)
    if for_update:
        stmt = stmt.with_for_update()
    payment = (await session.scalars(stmt)).first()
    if not payment or payment.org_id != org_id:
        raise NotFoundError("Payment not found")
    return payment


async def allocated_amount(session: AsyncSession, payment: Payment) -> Decimal:
    rows = await session.scalars(
        select(PaymentAllocation.amount).where(PaymentAllocation.payment_id == payment.id)
    )
    return sum((_d(a) for a in rows), ZERO)


async def _allocate(
    session: AsyncSession,
    org_id: uuid.UUID,
    payment: Payment,
    allocations: list[tuple[uuid.UUID, Decimal]],
) -> None:
    expected_type = "ar" if payment.direction == "in" else "ap"
    for invoice_id, alloc_amount in allocations:
        # Lock the invoice row: concurrent payments must serialize or the
        # amount_paid read-modify-write over-allocates.
        invoice = (
            await session.scalars(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
        ).first()
        if not invoice or invoice.org_id != org_id:
            raise ValidationError("Unknown invoice in allocations")
        if invoice.invoice_type != expected_type:
            raise ValidationError(
                f"Invoice {invoice.number} is not an {expected_type.upper()} invoice"
            )
        if invoice.party_id != payment.party_id:
            raise ValidationError(f"Invoice {invoice.number} belongs to a different party")
        if invoice.currency != payment.currency:
            raise ValidationError(
                f"Invoice {invoice.number} is in {invoice.currency}, payment in {payment.currency}"
            )
        if invoice.status not in ("posted", "partial"):
            raise ValidationError(f"Invoice {invoice.number} is not open for payment")
        due = open_balance(invoice)
        if _d(alloc_amount) > due:
            raise ValidationError(
                f"Allocation {alloc_amount} exceeds open balance {due} on {invoice.number}"
            )
        session.add(
            PaymentAllocation(payment_id=payment.id, invoice_id=invoice_id, amount=_d(alloc_amount))
        )
        await apply_allocation_to_invoice(invoice, _d(alloc_amount), sign=1)
    await session.flush()


async def record_payment(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    direction: str,
    party_id: uuid.UUID,
    party_name: str | None,
    payment_date,
    amount: Decimal,
    method: str,
    reference: str | None,
    notes: str | None,
    allocations: list[tuple[uuid.UUID, Decimal]],
    actor_id: uuid.UUID,
    prefix: str,
    credit_note_id: uuid.UUID | None = None,
    currency: str = "USD",
    fx_rate: Decimal = Decimal("1"),
) -> Payment:
    """Create a payment with allocations; updates invoice statuses in the same tx."""
    total_allocated = sum((_d(a) for _, a in allocations), ZERO)
    if total_allocated > _d(amount):
        raise ValidationError("Allocations exceed the payment amount")
    if credit_note_id and allocations:
        raise ValidationError("A refund cannot also be allocated to invoices")

    from app.shared.numbering import next_number

    number = await next_number(
        session,
        org_id,
        "customer_payment" if direction == "in" else "supplier_payment",
        prefix,
    )
    payment = Payment(
        org_id=org_id,
        number=number,
        direction="in" if direction == "in" else "out",
        party_id=party_id,
        party_name=party_name,
        payment_date=payment_date,
        currency=currency,
        fx_rate=fx_rate,
        amount=_d(amount),
        amount_base=(_d(amount) / fx_rate).quantize(Decimal("0.01")),
        method=method,
        reference=reference,
        notes=notes,
        created_by=actor_id,
        credit_note_id=credit_note_id,
    )
    session.add(payment)
    await session.flush()

    if credit_note_id:
        credit = await invoice_or_404(session, org_id, credit_note_id, for_update=True)
        credit.amount_paid = _d(credit.amount_paid) + _d(amount)
        refresh_settlement_status(credit)
    await _allocate(session, org_id, payment, allocations)
    return payment


async def allocate_existing_payment(
    session: AsyncSession,
    org_id: uuid.UUID,
    payment: Payment,
    allocations: list[tuple[uuid.UUID, Decimal]],
) -> None:
    """Apply a payment's unallocated (on-account) amount to open invoices."""
    if payment.status != "recorded":
        raise ConflictError("Only recorded payments can be allocated")
    if payment.credit_note_id:
        raise ConflictError("Refunds cannot be allocated to invoices")
    unallocated = _d(payment.amount) - await allocated_amount(session, payment)
    requested = sum((_d(a) for _, a in allocations), ZERO)
    if requested > unallocated:
        raise ValidationError(f"Only {unallocated} of {payment.number} is unallocated")
    await _allocate(session, org_id, payment, allocations)
