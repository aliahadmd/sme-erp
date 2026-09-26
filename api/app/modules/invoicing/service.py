"""Invoicing service — invoice lifecycle, payments, allocations."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.service import get_setting
from app.modules.invoicing.models import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.shared.order_engine import apply_line_math, resolve_line_references


async def invoice_or_404(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> Invoice:
    invoice = await session.get(Invoice, invoice_id)
    if not invoice or invoice.org_id != org_id:
        raise NotFoundError("Invoice not found")
    return invoice


async def build_lines_from_order(
    session: AsyncSession,
    order: object,
    is_purchase: bool,
    remaining: dict | None = None,
) -> list[InvoiceLine]:
    """Copy snapshot lines from a sales/purchase order, capped at the
    outstanding (not-yet-invoiced) quantity per product."""
    from decimal import Decimal

    lines: list[InvoiceLine] = []
    position = 0
    for line in order.lines:  # type: ignore[attr-defined]
        if line.product_id is None:
            qty = line.qty  # free-text lines have no progress tracking
        elif remaining is not None:
            qty = remaining.get(line.product_id, Decimal("0"))
        else:
            qty = line.qty
        if Decimal(str(qty)) <= 0:
            continue
        new_line = InvoiceLine(
            position=position,
            product_id=line.product_id,
            product_name=line.product_name,
            description=line.description,
            qty=qty,
            uom_code=line.uom_code,
            unit_price=line.unit_price,
            discount_pct=line.discount_pct,
            tax_id=line.tax_id,
            tax_rate_pct=line.tax_rate_pct,
        )
        apply_line_math(new_line)
        lines.append(new_line)
        position += 1
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
    entity = "ar_invoice" if invoice.invoice_type == "ar" else "ap_invoice"
    default_prefix = "INV" if invoice.invoice_type == "ar" else "BILL"
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
    await session.flush()
    return invoice


async def apply_allocation_to_invoice(invoice: Invoice, amount: Decimal, sign: int = 1) -> None:
    """Adjust paid amount and derived status. sign=-1 releases an allocation."""
    invoice.amount_paid = Decimal(str(invoice.amount_paid)) + sign * Decimal(str(amount))
    total = Decimal(str(invoice.total))
    if invoice.status == "void":
        raise ConflictError("Cannot allocate against a void invoice")
    if invoice.amount_paid >= total:
        invoice.status = "paid"
    elif invoice.amount_paid > 0:
        invoice.status = "partial"
    else:
        invoice.status = "posted"


async def payment_or_404(
    session: AsyncSession, org_id: uuid.UUID, payment_id: uuid.UUID
) -> Payment:
    payment = await session.get(Payment, payment_id)
    if not payment or payment.org_id != org_id:
        raise NotFoundError("Payment not found")
    return payment


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
) -> Payment:
    """Create a payment with allocations; updates invoice statuses in the same tx."""
    total_allocated = sum((Decimal(str(a)) for _, a in allocations), Decimal("0"))
    if total_allocated > Decimal(str(amount)):
        raise ValidationError("Allocations exceed the payment amount")

    expected_direction = "in" if direction == "in" else "out"
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
        direction=expected_direction,
        party_id=party_id,
        party_name=party_name,
        payment_date=payment_date,
        amount=Decimal(str(amount)),
        method=method,
        reference=reference,
        notes=notes,
        created_by=actor_id,
    )
    session.add(payment)
    await session.flush()

    for invoice_id, alloc_amount in allocations:
        # Lock the invoice row: concurrent payments must serialize or the
        # amount_paid read-modify-write over-allocates.
        invoice = (
            await session.scalars(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
        ).first()
        if not invoice or invoice.org_id != org_id:
            raise ValidationError("Unknown invoice in allocations")
        expected_type = "ar" if direction == "in" else "ap"
        if invoice.invoice_type != expected_type:
            raise ValidationError(
                f"Invoice {invoice.number} is not an {expected_type.upper()} invoice"
            )
        if invoice.status not in ("posted", "partial"):
            raise ValidationError(f"Invoice {invoice.number} is not open for payment")
        due = Decimal(str(invoice.total)) - Decimal(str(invoice.amount_paid))
        if Decimal(str(alloc_amount)) > due:
            raise ValidationError(
                f"Allocation {alloc_amount} exceeds open balance {due} on {invoice.number}"
            )
        session.add(
            PaymentAllocation(
                payment_id=payment.id, invoice_id=invoice_id, amount=Decimal(str(alloc_amount))
            )
        )
        await apply_allocation_to_invoice(invoice, Decimal(str(alloc_amount)), sign=1)
    return payment
