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
    flag = "is_customer" if body.invoice_type == "ar" else "is_supplier"
    if not getattr(party, flag):
        raise ValidationError(f"Contact is not a {_expected_party_type(body.invoice_type)}")

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
    if body.source_order_id:
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
        if order.status not in ("confirmed", "delivered", "received"):
            raise ConflictError("Only confirmed/processed orders can be invoiced")
        invoice.source_type = source_type
        invoice.source_id = order.id
        invoice.source_number = order.number
        lines = await build_lines_from_order(session, order, body.invoice_type == "ap")
    else:
        if not body.lines:
            raise ValidationError("Standalone invoice needs at least one line")
        lines = await build_lines_from_input(session, org.id, body.lines, body.invoice_type == "ap")

    # Assign while the invoice is still transient — after flush the assignment
    # would trigger a sync lazy-load (MissingGreenlet).
    invoice.lines = lines
    from app.shared.order_engine import recompute_header

    recompute_header(invoice, lines)
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
    if invoice.source_id and invoice.source_type == "sales_order":
        from app.modules.sales import service as sales_service

        await sales_service.mark_status(session, org.id, invoice.source_id, "invoiced")
    elif invoice.source_id and invoice.source_type == "purchase_order":
        from app.modules.purchasing import service as purchasing_service

        await purchasing_service.mark_status(session, org.id, invoice.source_id, "invoiced")
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
                "method": payment.method,
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
        invoice = await session.get(Invoice, allocation.invoice_id)
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
