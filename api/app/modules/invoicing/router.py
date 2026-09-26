"""Invoicing API: invoices (AR/AP), payments, allocations, statements."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import (
    get_organization,
    write_audit,
)
from app.modules.crm.models import Contact
from app.modules.invoicing import schemas as inv
from app.modules.invoicing.models import Invoice, Payment
from app.modules.invoicing.service import (
    apply_allocation_to_invoice,
    build_lines_from_input,
    build_lines_from_invoice,
    build_lines_from_order,
    invoice_or_404,
    payment_or_404,
    post_invoice,
    record_payment,
)
from app.shared.events import Event, publish
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/invoicing")


async def _party(session: AsyncSession, party_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, party_id)
    if not contact or contact.status != "active":
        raise ValidationError("Invoice requires an active contact")
    return contact


def _expected_party_type(invoice_type: str) -> str:
    return "customer" if invoice_type == "ar" else "supplier"


# ------------------------------------------------------------------ invoices
@router.get("/invoices", response_model=inv.InvoicePage)
async def list_invoices(
    params: PageParamsDep,
    invoice_type: str | None = Query(None, pattern=r"^(ar|ap)$"),
    status: str | None = None,
    party_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=60),
    _user: CurrentUser = Depends(require("invoicing.invoice.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.InvoicePage:
    org = await get_organization(session)
    stmt = select(Invoice).where(Invoice.org_id == org.id).order_by(Invoice.created_at.desc())
    if invoice_type:
        stmt = stmt.where(Invoice.invoice_type == invoice_type)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if party_id:
        stmt = stmt.where(Invoice.party_id == party_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Invoice.number.ilike(like) | Invoice.party_name.ilike(like))
    rows, total = await paginate(session, stmt, params)
    return inv.InvoicePage(
        items=[inv.InvoiceOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/invoices/{invoice_id}", response_model=inv.InvoiceOut)
async def get_invoice(
    invoice_id: uuid.UUID,
    _user: CurrentUser = Depends(require("invoicing.invoice.read")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    return await invoice_or_404(session, org.id, invoice_id)


@router.post("/invoices", response_model=inv.InvoiceOut, status_code=201)
async def create_invoice(
    body: inv.InvoiceCreateIn,
    user: CurrentUser = Depends(require("invoicing.invoice.create")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    party = await _party(session, body.party_id)
    is_credit = body.invoice_type.endswith("_credit")
    base_type = "ar" if body.invoice_type.startswith("ar") else "ap"
    flag = "is_customer" if base_type == "ar" else "is_supplier"
    if not getattr(party, flag):
        raise ValidationError(f"Contact is not a {_expected_party_type(base_type)}")
    if is_credit and not body.original_invoice_id:
        raise ValidationError("Credit notes must reference the original invoice")

    invoice = Invoice(
        org_id=org.id,
        invoice_type=body.invoice_type,
        party_id=party.id,
        party_name=party.name,
        invoice_date=body.invoice_date or date.today(),
        due_date=body.due_date,
        currency=body.currency,
        notes=body.notes,
    )
    original = None
    lines: list = []
    if body.original_invoice_id:
        # Credit note: mirrors the original invoice's lines.
        original = await invoice_or_404(session, org.id, body.original_invoice_id)
        if original.status not in ("posted", "partial", "paid"):
            raise ConflictError("Can only credit posted invoices")
        if body.lines:
            lines = await build_lines_from_input(session, org.id, body.lines, False)
        else:
            lines = build_lines_from_invoice(original)
    elif body.source_order_id:
        source_type = "sales_order" if body.invoice_type == "ar" else "purchase_order"
        module = {
            "sales_order": "app.modules.sales.models",
            "purchase_order": "app.modules.purchasing.models",
        }[source_type]
        model_name = "SalesOrder" if source_type == "sales_order" else "PurchaseOrder"
        order_model = getattr(__import__(module, fromlist=[model_name]), model_name)
        order = await session.get(order_model, body.source_order_id)
        if not order or order.org_id != org.id:
            raise NotFoundError("Source order not found")
        if order.status not in ("confirmed", "delivered", "received", "invoiced"):
            raise ConflictError("Only confirmed/processed orders can be invoiced")
        from app.shared.order_progress import remaining_by_product

        progress_field = "qty_invoiced"
        remaining = remaining_by_product(order, progress_field)
        if remaining and all(q <= 0 for q in remaining.values()):
            raise ConflictError(f"Order {order.number} is fully invoiced")
        invoice.source_type = source_type
        invoice.source_id = order.id
        invoice.source_number = order.number
        lines = await build_lines_from_order(
            session, order, body.invoice_type == "ap", remaining=remaining
        )
        if not lines:
            raise ConflictError("No outstanding quantities left to invoice")
    else:
        if not body.lines:
            raise ValidationError("Standalone invoice needs at least one line")
        lines = await build_lines_from_input(session, org.id, body.lines, body.invoice_type == "ap")

    # Assign while the invoice is still transient — after flush the assignment
    # would trigger a sync lazy-load (MissingGreenlet).
    invoice.lines = lines
    from app.modules.currencies.service import resolve_rate, to_base
    from app.shared.order_engine import recompute_header

    recompute_header(invoice, lines)
    rate = await resolve_rate(
        session, org.id, body.currency or invoice.currency, invoice.invoice_date
    )
    invoice.fx_rate = rate
    invoice.total_base = to_base(invoice.total, invoice.fx_rate)
    if is_credit and original is not None:
        already_credited = sum(
            (
                Decimal(str(c.total))
                for c in (
                    await session.scalars(
                        select(Invoice).where(
                            Invoice.original_invoice_id == original.id,
                            Invoice.status.in_(("posted", "partial", "paid")),
                        )
                    )
                )
            ),
            Decimal("0"),
        )
        if already_credited + invoice.total > original.total:
            raise ConflictError(
                f"Credit exceeds the invoice total (already credited {already_credited})"
            )
    # default due date from payment terms
    if invoice.due_date is None and party.payment_terms_days:
        from datetime import timedelta

        invoice.due_date = invoice.invoice_date + timedelta(days=party.payment_terms_days)
    session.add(invoice)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"party": party.name, "total": str(invoice.total)},
    )
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.patch("/invoices/{invoice_id}", response_model=inv.InvoiceOut)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: inv.InvoiceCreateIn,
    user: CurrentUser = Depends(require("invoicing.invoice.update")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("Only draft invoices can be edited")
    party = await _party(session, body.party_id)
    invoice.party_id = party.id
    invoice.party_name = party.name
    invoice.notes = body.notes
    invoice.invoice_date = body.invoice_date or invoice.invoice_date
    invoice.due_date = body.due_date
    if not body.lines:
        raise ValidationError("Invoice needs at least one line")
    invoice.lines = await build_lines_from_input(
        session, org.id, body.lines, invoice.invoice_type == "ap"
    )
    from app.shared.order_engine import recompute_header

    recompute_header(invoice, invoice.lines)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
    )
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/post", response_model=inv.InvoiceOut)
async def post_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.post")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    await post_invoice(session, org.id, invoice, user.id)
    if invoice.source_id:
        # Register invoiced quantities; the service moves the order to
        # `invoiced` and to `closed` when receipt/delivery + invoicing complete.
        per_product: dict = {}
        for line in invoice.lines:
            if line.product_id is None:
                continue
            per_product[line.product_id] = per_product.get(line.product_id, 0) + line.qty
        amounts = list(per_product.items())
        if invoice.source_type == "sales_order":
            from app.modules.sales import service as sales_service

            await sales_service.register_invoiced(session, org.id, invoice.source_id, amounts)
        else:
            from app.modules.purchasing import service as purchasing_service

            await purchasing_service.register_invoiced(session, org.id, invoice.source_id, amounts)
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"number": invoice.number, "total": str(invoice.total)},
    )
    await session.commit()
    await publish(
        Event(
            name="invoice.posted",
            payload={
                "invoice_id": str(invoice.id),
                "number": invoice.number,
                "invoice_type": invoice.invoice_type,
                "party_id": str(invoice.party_id),
                "total_base": str(invoice.total_base),
                "net_base": str(
                    (Decimal(str(invoice.subtotal)) - Decimal(str(invoice.discount_total)))
                    / invoice.fx_rate
                ),
                "tax_base": str(Decimal(str(invoice.tax_total)) / invoice.fx_rate),
                "total": str(invoice.total),
                "subtotal": str(invoice.subtotal),
                "discount_total": str(invoice.discount_total),
                "tax_total": str(invoice.tax_total),
                "lines": [
                    {
                        "product_id": str(line.product_id) if line.product_id else None,
                        "qty": str(line.qty),
                        "unit_price": str(line.unit_price),
                        "line_subtotal": str(line.line_subtotal),
                        "tax_id": str(line.tax_id) if line.tax_id else None,
                        "tax_rate_pct": str(line.tax_rate_pct),
                        "line_tax": str(line.line_tax),
                    }
                    for line in invoice.lines
                ],
            },
            org_id=org.id,
        )
    )
    await session.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/void", response_model=inv.InvoiceOut)
async def void_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.void")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status == "void":
        raise ConflictError("Invoice is already void")
    if invoice.status == "draft":
        raise ConflictError("Draft invoices can simply be deleted — post first to void")
    if Decimal(str(invoice.amount_paid)) > 0:
        raise ConflictError("Invoice has payments allocated — void them first")
    invoice.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"number": invoice.number},
    )
    await session.commit()
    await publish(
        Event(
            name="invoice.voided",
            payload={
                "invoice_id": str(invoice.id),
                "number": invoice.number,
                "invoice_type": invoice.invoice_type,
                "total_base": str(invoice.total_base),
                "net_base": str(
                    (Decimal(str(invoice.subtotal)) - Decimal(str(invoice.discount_total)))
                    / invoice.fx_rate
                ),
                "tax_base": str(Decimal(str(invoice.tax_total)) / invoice.fx_rate),
                "total": str(invoice.total),
                "subtotal": str(invoice.subtotal),
                "tax_total": str(invoice.tax_total),
                "discount_total": str(invoice.discount_total),
            },
            org_id=org.id,
        )
    )
    await session.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/send-email")
async def email_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue the invoice email to the customer's billing contact."""
    from app.jobs.queue import enqueue

    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status == "draft":
        raise ValidationError("Post the invoice before emailing it")
    if not invoice.party_id:
        raise NotFoundError("Invoice has no customer")
    party = await session.get(Contact, invoice.party_id)
    to = None
    if party and party.emails:
        to = party.emails[0].get("value")
    if not to:
        raise ValidationError("Customer has no email address")
    await enqueue(
        "send_email",
        to=to,
        subject=f"Invoice {invoice.number} — {invoice.total}",
        body="Please find your invoice attached.",
    )
    await write_audit(
        session,
        actor=user.user,
        action="email",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"to": to},
    )
    await session.commit()
    return {"status": "queued", "to": to}


# ------------------------------------------------------------------ payments
@router.get("/payments", response_model=inv.PaymentPage)
async def list_payments(
    params: PageParamsDep,
    direction: str | None = Query(None, pattern=r"^(in|out)$"),
    party_id: uuid.UUID | None = None,
    _user: CurrentUser = Depends(require("invoicing.payment.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.PaymentPage:
    org = await get_organization(session)
    stmt = select(Payment).where(Payment.org_id == org.id).order_by(Payment.created_at.desc())
    if direction:
        stmt = stmt.where(Payment.direction == direction)
    if party_id:
        stmt = stmt.where(Payment.party_id == party_id)
    rows, total = await paginate(session, stmt, params)
    return inv.PaymentPage(
        items=[inv.PaymentOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/payments", response_model=inv.PaymentOut, status_code=201)
async def create_payment(
    body: inv.PaymentIn,
    user: CurrentUser = Depends(require("invoicing.payment.create")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    org = await get_organization(session)
    party = await _party(session, body.party_id)
    # Refund: money out against a posted AR credit note
    credit_note = None
    if body.credit_note_id:
        if body.direction != "out":
            raise ValidationError("Refunds are recorded as payments going out")
        credit_note = await invoice_or_404(session, org.id, body.credit_note_id)
        if not credit_note.invoice_type.startswith("ar_credit") or credit_note.status != "posted":
            raise ValidationError("Refunds must reference a posted AR credit note")
        refunded = (
            await session.scalars(
                select(Payment).where(
                    Payment.credit_note_id == credit_note.id,
                    Payment.status == "recorded",
                )
            )
        ).all()
        already_refunded = sum((p.amount for p in refunded), Decimal("0"))
        if already_refunded + body.amount > credit_note.total:
            raise ValidationError("Refund exceeds the credit note total")

    # Payment currency follows the allocated invoices (all must match);
    # base-currency rate is 1 by definition.
    from app.modules.currencies.service import resolve_rate
    from app.modules.invoicing.models import Invoice

    currencies = {(await session.get(Invoice, a.invoice_id)).currency for a in body.allocations}
    if len(currencies) > 1:
        raise ValidationError("Allocations must reference invoices of one currency")
    payment_currency = currencies.pop() if currencies else org.base_currency
    rate = await resolve_rate(session, org.id, payment_currency, body.payment_date or date.today())

    prefixes = {"in": "PAY", "out": "SPAY"}
    payment = await record_payment(
        session,
        org.id,
        direction=body.direction,
        party_id=party.id,
        party_name=party.name,
        payment_date=body.payment_date or date.today(),
        amount=body.amount,
        method=body.method,
        reference=body.reference,
        notes=body.notes,
        allocations=[(a.invoice_id, a.amount) for a in body.allocations],
        actor_id=user.id,
        prefix=prefixes[body.direction],
        currency=payment_currency,
        fx_rate=rate,
        credit_note_id=body.credit_note_id,
    )
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="invoicing.payment",
        entity_id=payment.id,
        after={"number": payment.number, "amount": str(payment.amount)},
    )
    await session.commit()
    # Re-fetch with allocations loaded — post-commit relationship access would
    # otherwise trigger a sync lazy-load.
    from sqlalchemy.orm import selectinload

    payment = (
        await session.scalars(
            select(Payment)
            .where(Payment.id == payment.id)
            .options(selectinload(Payment.allocations))
        )
    ).first()
    await publish(
        Event(
            name="payment.recorded",
            payload={
                "payment_id": str(payment.id),
                "number": payment.number,
                "direction": payment.direction,
                "party_id": str(party.id),
                "amount": str(payment.amount),
                "amount_base": str(payment.amount_base),
                "method": payment.method,
                "credit_note_id": str(payment.credit_note_id) if payment.credit_note_id else None,
                "allocations": [
                    {"invoice_id": str(a.invoice_id), "amount": str(a.amount)}
                    for a in payment.allocations
                ],
            },
            org_id=org.id,
        )
    )
    return payment


@router.post("/payments/{payment_id}/void", response_model=inv.PaymentOut)
async def void_payment(
    payment_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.payment.void")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    org = await get_organization(session)
    payment = await payment_or_404(session, org.id, payment_id)
    if payment.status == "void":
        raise ConflictError("Payment is already void")
    for allocation in payment.allocations:
        invoice = (
            await session.scalars(
                select(Invoice).where(Invoice.id == allocation.invoice_id).with_for_update()
            )
        ).first()
        if invoice:
            await apply_allocation_to_invoice(invoice, allocation.amount, sign=-1)
    payment.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="invoicing.payment",
        entity_id=payment.id,
        after={"number": payment.number},
    )
    await session.commit()
    await publish(
        Event(
            name="payment.voided",
            payload={
                "payment_id": str(payment.id),
                "number": payment.number,
                "direction": payment.direction,
                "amount": str(payment.amount),
                "method": payment.method,
            },
            org_id=org.id,
        )
    )
    await session.refresh(payment)
    return payment


# ----------------------------------------------------------------- statement
@router.get("/statement/{party_id}", response_model=inv.StatementOut)
async def party_statement(
    party_id: uuid.UUID,
    _user: CurrentUser = Depends(require("invoicing.invoice.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.StatementOut:
    org = await get_organization(session)
    party = await session.get(Contact, party_id)
    if not party:
        raise NotFoundError("Contact not found")
    invoices = (
        await session.scalars(
            select(Invoice)
            .where(
                Invoice.org_id == org.id,
                Invoice.party_id == party_id,
                Invoice.status.in_(("posted", "partial")),
            )
            .order_by(Invoice.invoice_date)
        )
    ).all()
    lines = [
        inv.StatementLine(
            invoice_id=invoice.id,
            number=invoice.number,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            total=invoice.total,
            amount_paid=invoice.amount_paid,
            balance=Decimal(str(invoice.total)) - Decimal(str(invoice.amount_paid)),
            status=invoice.status,
        )
        for invoice in invoices
    ]
    return inv.StatementOut(
        party_id=party_id,
        party_name=party.name,
        open_balance=sum((line.balance for line in lines), Decimal("0.00")).quantize(
            Decimal("0.01")
        ),
        invoices=lines,
    )
