"""Invoicing API: invoices (AR/AP), credit notes, payments, allocations, statements.

Journal entries are posted by transactional subscribers (`emit` before
commit) — a document and its journal entry are saved atomically.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.modules.crm.models import Contact
from app.modules.currencies.service import resolve_rate, to_base
from app.modules.invoicing import schemas as inv
from app.modules.invoicing.models import Invoice, Payment
from app.modules.invoicing.service import (
    allocate_existing_payment,
    apply_allocation_to_invoice,
    build_lines_from_input,
    build_lines_from_invoice,
    build_lines_from_order,
    invoice_or_404,
    is_credit,
    open_balance,
    order_amounts,
    payment_or_404,
    post_invoice,
    record_payment,
    refresh_settlement_status,
    void_invoice_settlement,
)
from app.shared.events import Event, emit, publish
from app.shared.order_engine import recompute_header
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/invoicing")

ZERO = Decimal("0")


async def _party(session: AsyncSession, party_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, party_id)
    if not contact or contact.status != "active":
        raise ValidationError("Invoice requires an active contact")
    return contact


def _check_party_role(party: Contact, invoice_type: str) -> None:
    if invoice_type.startswith("ar") and not party.is_customer:
        raise ValidationError("Contact is not a customer")
    if invoice_type.startswith("ap") and not party.is_supplier:
        raise ValidationError("Contact is not a supplier")


async def _default_currency(session: AsyncSession, party: Contact | None) -> str:
    if party is not None and party.currency:
        return party.currency
    return (await get_organization(session)).base_currency


async def _snapshot_fx(session: AsyncSession, org_id: uuid.UUID, invoice: Invoice) -> None:
    """(Re)compute header totals + the FX snapshot at the invoice date."""
    recompute_header(invoice, invoice.lines)
    invoice.fx_rate = await resolve_rate(session, org_id, invoice.currency, invoice.invoice_date)
    invoice.total_base = to_base(invoice.total, invoice.fx_rate)


def _invoice_event(name: str, invoice: Invoice, org_id: uuid.UUID) -> Event:
    return Event(
        name=name,
        payload={
            "invoice_id": str(invoice.id),
            "number": invoice.number,
            "invoice_type": invoice.invoice_type,
            "party_id": str(invoice.party_id) if invoice.party_id else None,
            "party_name": invoice.party_name,
            "currency": invoice.currency,
            "total": str(invoice.total),
            "total_base": str(invoice.total_base),
        },
        org_id=org_id,
    )


async def _order_for(session: AsyncSession, org_id: uuid.UUID, invoice_type: str, order_id):  # noqa: ANN001
    if invoice_type.startswith("ar"):
        from app.modules.sales.models import SalesOrder as model
    else:
        from app.modules.purchasing.models import PurchaseOrder as model
    order = await session.get(model, order_id)
    if not order or order.org_id != org_id:
        raise NotFoundError("Source order not found")
    return order


# ------------------------------------------------------------------ invoices
@router.get("/invoices", response_model=inv.InvoicePage)
async def list_invoices(
    params: PageParamsDep,
    invoice_type: str | None = Query(None, pattern=r"^(ar|ap|ar_credit|ap_credit)$"),
    side: str | None = Query(None, pattern=r"^(ar|ap)$", description="ar/ap incl. credit notes"),
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
    if side:
        stmt = stmt.where(Invoice.invoice_type.in_((side, f"{side}_credit")))
    if status:
        stmt = stmt.where(Invoice.status == status)
    if party_id:
        stmt = stmt.where(Invoice.party_id == party_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.party_name.ilike(like)))
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
    _check_party_role(party, body.invoice_type)
    credit = body.invoice_type.endswith("_credit")
    base_type = body.invoice_type.removesuffix("_credit")
    if credit and not body.original_invoice_id:
        raise ValidationError("Credit notes must reference the original invoice")
    if not credit and body.original_invoice_id:
        raise ValidationError("Only credit notes reference an original invoice")

    invoice = Invoice(
        org_id=org.id,
        invoice_type=body.invoice_type,
        party_id=party.id,
        party_name=party.name,
        invoice_date=body.invoice_date or date.today(),
        due_date=body.due_date,
        notes=body.notes,
    )
    currency = body.currency
    if credit:
        original = await invoice_or_404(session, org.id, body.original_invoice_id)
        if original.invoice_type != base_type:
            raise ValidationError(f"A {body.invoice_type} must credit a {base_type} invoice")
        if original.party_id != party.id:
            raise ValidationError("Credit note party must match the original invoice")
        if original.status not in ("posted", "partial", "paid"):
            raise ConflictError("Can only credit posted invoices")
        if currency and currency != original.currency:
            raise ValidationError("Credit notes use the original invoice's currency")
        currency = original.currency
        invoice.original_invoice_id = original.id
        if body.lines:
            lines = await build_lines_from_input(
                session, org.id, body.lines, is_purchase=base_type == "ap"
            )
        else:
            lines = build_lines_from_invoice(original)
    elif body.source_order_id:
        order = await _order_for(session, org.id, body.invoice_type, body.source_order_id)
        if order.status not in ("confirmed", "delivered", "received", "invoiced"):
            raise ConflictError("Only confirmed/processed orders can be invoiced")
        if currency and currency != order.currency:
            raise ValidationError("Invoices use the source order's currency")
        currency = order.currency
        invoice.source_type = "sales_order" if base_type == "ar" else "purchase_order"
        invoice.source_id = order.id
        invoice.source_number = order.number
        lines = build_lines_from_order(order)
        if not lines:
            raise ConflictError(f"Order {order.number} is fully invoiced")
    else:
        if not body.lines:
            raise ValidationError("Standalone invoice needs at least one line")
        lines = await build_lines_from_input(
            session, org.id, body.lines, is_purchase=base_type == "ap"
        )

    invoice.currency = currency or await _default_currency(session, party)
    # Assign while the invoice is still transient — after flush the assignment
    # would trigger a sync lazy-load (MissingGreenlet).
    invoice.lines = lines
    await _snapshot_fx(session, org.id, invoice)
    if credit:
        # Early feedback against already-posted credits; the binding check
        # (with a row lock) runs again when the credit note is posted.
        posted_credits = await session.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.original_invoice_id == invoice.original_invoice_id,
                Invoice.status.in_(("posted", "partial", "paid")),
            )
        )
        if Decimal(str(posted_credits)) + invoice.total > original.total:
            raise ConflictError(
                f"Credit exceeds the invoice total (already credited {posted_credits})"
            )
    if invoice.due_date is None and party.payment_terms_days and not credit:
        invoice.due_date = invoice.invoice_date + timedelta(days=party.payment_terms_days)
    session.add(invoice)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"party": party.name, "total": str(invoice.total), "currency": invoice.currency},
    )
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.patch("/invoices/{invoice_id}", response_model=inv.InvoiceOut)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: inv.InvoiceUpdateIn,
    user: CurrentUser = Depends(require("invoicing.invoice.update")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("Only draft invoices can be edited")
    data = body.model_dump(exclude_unset=True)
    if data.get("party_id"):
        party = await _party(session, body.party_id)
        _check_party_role(party, invoice.invoice_type)
        invoice.party_id = party.id
        invoice.party_name = party.name
    if "currency" in data and body.currency and body.currency != invoice.currency:
        if invoice.original_invoice_id or invoice.source_id:
            raise ValidationError("The currency follows the source document")
        invoice.currency = body.currency
    if body.invoice_date:
        invoice.invoice_date = body.invoice_date
    for field in ("due_date", "notes"):
        if field in data:
            setattr(invoice, field, data[field])
    if "lines" in data:
        if not body.lines:
            raise ValidationError("Invoice needs at least one line")
        invoice.lines = await build_lines_from_input(
            session, org.id, body.lines, invoice.invoice_type.startswith("ap")
        )
    # Totals AND the FX snapshot always follow the edit — a stale total_base
    # would post an unbalanced journal entry.
    await _snapshot_fx(session, org.id, invoice)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"total": str(invoice.total), "currency": invoice.currency},
    )
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_draft_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.update")),
    session: AsyncSession = Depends(get_session),
) -> None:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("Only draft invoices can be deleted — void posted ones")
    await write_audit(
        session,
        actor=user.user,
        action="delete",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        before={"party": invoice.party_name, "total": str(invoice.total)},
    )
    await session.delete(invoice)
    await session.commit()


async def _register_order(
    session: AsyncSession, org_id: uuid.UUID, invoice: Invoice, release: bool
):
    if not invoice.source_id:
        return
    by_line, by_product = order_amounts(invoice)
    if invoice.source_type == "sales_order":
        from app.modules.sales import service as order_service
    else:
        from app.modules.purchasing import service as order_service
    fn = order_service.release_invoiced if release else order_service.register_invoiced
    await fn(session, org_id, invoice.source_id, by_line=by_line, by_product=by_product)


@router.post("/invoices/{invoice_id}/post", response_model=inv.InvoiceOut)
async def post_invoice_endpoint(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.post")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id, for_update=True)
    await post_invoice(session, org.id, invoice, user.id)
    await _register_order(session, org.id, invoice, release=False)
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"number": invoice.number, "total": str(invoice.total)},
    )
    event = _invoice_event("invoice.posted", invoice, org.id)
    await emit(session, event)  # journal entry — same transaction
    await session.commit()
    await publish(event)
    await session.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/void", response_model=inv.InvoiceOut)
async def void_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.void")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id, for_update=True)
    if invoice.status == "void":
        raise ConflictError("Invoice is already void")
    if invoice.status == "draft":
        raise ConflictError("Draft invoices are deleted, not voided")
    await void_invoice_settlement(session, org.id, invoice)
    await _register_order(session, org.id, invoice, release=True)
    invoice.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="invoicing.invoice",
        entity_id=invoice.id,
        after={"number": invoice.number},
    )
    event = _invoice_event("invoice.voided", invoice, org.id)
    await emit(session, event)
    await session.commit()
    await publish(event)
    await session.refresh(invoice)
    return invoice


@router.post("/invoices/{invoice_id}/send-email")
async def email_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.invoice.post")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue the invoice email to the customer's billing contact."""
    from app.jobs.queue import enqueue
    from app.modules.core.notification_events import document_email, party_email

    org = await get_organization(session)
    invoice = await invoice_or_404(session, org.id, invoice_id)
    if invoice.status == "draft":
        raise ValidationError("Post the invoice before emailing it")
    if not invoice.invoice_type.startswith("ar"):
        raise ValidationError("Only customer invoices and credit notes are emailed")
    party = await session.get(Contact, invoice.party_id) if invoice.party_id else None
    to = party_email(party)
    if not to:
        raise ValidationError("Customer has no valid email address")
    subject, body = document_email(invoice, org.name)
    await enqueue("send_email", to=to, subject=subject, body=body)
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


def _payment_event(name: str, payment: Payment, org_id: uuid.UUID) -> Event:
    return Event(
        name=name,
        payload={
            "payment_id": str(payment.id),
            "number": payment.number,
            "direction": payment.direction,
            "party_id": str(payment.party_id),
            "currency": payment.currency,
            "amount": str(payment.amount),
            "amount_base": str(payment.amount_base),
            "method": payment.method,
            "credit_note_id": str(payment.credit_note_id) if payment.credit_note_id else None,
        },
        org_id=org_id,
    )


async def _reload_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment:
    # Re-fetch with allocations loaded — post-commit relationship access would
    # otherwise trigger a sync lazy-load.
    from sqlalchemy.orm import selectinload

    return (
        await session.scalars(
            select(Payment)
            .where(Payment.id == payment_id)
            .options(selectinload(Payment.allocations))
            .execution_options(populate_existing=True)
        )
    ).one()


@router.post("/payments", response_model=inv.PaymentOut, status_code=201)
async def create_payment(
    body: inv.PaymentIn,
    user: CurrentUser = Depends(require("invoicing.payment.create")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    org = await get_organization(session)
    party = await _party(session, body.party_id)
    payment_date = body.payment_date or date.today()

    currencies: set[str] = set()
    for allocation in body.allocations:
        invoice = await session.get(Invoice, allocation.invoice_id)
        if not invoice or invoice.org_id != org.id:
            raise ValidationError("Unknown invoice in allocations")
        currencies.add(invoice.currency)
    if body.credit_note_id:
        # Refund: AR credit note → money out; AP credit note → money in.
        credit_note = await invoice_or_404(session, org.id, body.credit_note_id)
        if not is_credit(credit_note) or credit_note.status not in ("posted", "partial"):
            raise ValidationError("Refunds must reference an open posted credit note")
        if credit_note.party_id != party.id:
            raise ValidationError("Refund party must match the credit note")
        expected = "out" if credit_note.invoice_type == "ar_credit" else "in"
        if body.direction != expected:
            raise ValidationError(
                f"Refunds of {credit_note.invoice_type} credit notes are money '{expected}'"
            )
        refundable = open_balance(credit_note)
        if body.amount > refundable:
            raise ValidationError(f"Refund exceeds the credit note's open amount {refundable}")
        currencies.add(credit_note.currency)
    if body.currency:
        currencies.add(body.currency)
    if len(currencies) > 1:
        raise ValidationError("A payment and its documents must share one currency")
    currency = currencies.pop() if currencies else await _default_currency(session, party)
    rate = await resolve_rate(session, org.id, currency, payment_date)

    payment = await record_payment(
        session,
        org.id,
        direction=body.direction,
        party_id=party.id,
        party_name=party.name,
        payment_date=payment_date,
        amount=body.amount,
        method=body.method,
        reference=body.reference,
        notes=body.notes,
        allocations=[(a.invoice_id, a.amount) for a in body.allocations],
        actor_id=user.id,
        prefix="PAY" if body.direction == "in" else "SPAY",
        credit_note_id=body.credit_note_id,
        currency=currency,
        fx_rate=rate,
    )
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="invoicing.payment",
        entity_id=payment.id,
        after={"number": payment.number, "amount": str(payment.amount), "currency": currency},
    )
    event = _payment_event("payment.recorded", payment, org.id)
    await emit(session, event)
    await session.commit()
    await publish(event)
    return await _reload_payment(session, payment.id)


@router.post("/payments/{payment_id}/allocate", response_model=inv.PaymentOut)
async def allocate_payment(
    payment_id: uuid.UUID,
    body: inv.PaymentAllocationsIn,
    user: CurrentUser = Depends(require("invoicing.payment.create")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    """Apply an on-account (unallocated) payment amount to open invoices."""
    org = await get_organization(session)
    payment = await payment_or_404(session, org.id, payment_id, for_update=True)
    allocations = [(a.invoice_id, a.amount) for a in body.allocations]
    await allocate_existing_payment(session, org.id, payment, allocations)
    await write_audit(
        session,
        actor=user.user,
        action="allocate",
        entity_type="invoicing.payment",
        entity_id=payment.id,
        after={"allocations": [[str(i), str(a)] for i, a in allocations]},
    )
    for invoice_id, amount in allocations:
        await emit(
            session,
            Event(
                name="payment.allocated",
                payload={
                    "payment_id": str(payment.id),
                    "invoice_id": str(invoice_id),
                    "amount": str(amount),
                },
                org_id=org.id,
            ),
        )
    await session.commit()
    return await _reload_payment(session, payment.id)


@router.post("/payments/{payment_id}/void", response_model=inv.PaymentOut)
async def void_payment(
    payment_id: uuid.UUID,
    user: CurrentUser = Depends(require("invoicing.payment.void")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    org = await get_organization(session)
    payment = await _reload_payment(session, payment_id)
    if payment.org_id != org.id:
        raise NotFoundError("Payment not found")
    if payment.status == "void":
        raise ConflictError("Payment is already void")
    for allocation in payment.allocations:
        invoice = await invoice_or_404(session, org.id, allocation.invoice_id, for_update=True)
        await apply_allocation_to_invoice(invoice, allocation.amount, sign=-1)
    if payment.credit_note_id:
        credit = await invoice_or_404(session, org.id, payment.credit_note_id, for_update=True)
        credit.amount_paid = max(Decimal(str(credit.amount_paid)) - payment.amount, ZERO)
        refresh_settlement_status(credit)
    payment.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="invoicing.payment",
        entity_id=payment.id,
        after={"number": payment.number},
    )
    event = _payment_event("payment.voided", payment, org.id)
    await emit(session, event)
    await session.commit()
    await publish(event)
    return await _reload_payment(session, payment.id)


# ----------------------------------------------------------------- statement
@router.get("/statement/{party_id}", response_model=inv.StatementOut)
async def party_statement(
    party_id: uuid.UUID,
    _user: CurrentUser = Depends(require("invoicing.invoice.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.StatementOut:
    """Open items of a party in document currency: open invoices, credit
    notes not yet consumed, and on-account payment amounts."""
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
    lines = []
    open_total = ZERO
    for invoice in invoices:
        balance = open_balance(invoice)
        if is_credit(invoice):
            balance = -balance  # unconsumed credit reduces what is owed
        open_total += balance
        lines.append(
            inv.StatementLine(
                invoice_id=invoice.id,
                invoice_type=invoice.invoice_type,
                number=invoice.number,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                total=invoice.total,
                total_base=invoice.total_base,
                amount_paid=invoice.amount_paid,
                balance=balance,
                status=invoice.status,
            )
        )
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.org_id == org.id,
                Payment.party_id == party_id,
                Payment.status == "recorded",
                Payment.credit_note_id.is_(None),
            )
        )
    ).all()
    unapplied = sum((inv.PaymentOut.model_validate(p).unallocated for p in payments), ZERO)
    return inv.StatementOut(
        party_id=party_id,
        party_name=party.name,
        open_balance=(open_total - unapplied).quantize(Decimal("0.01")),
        unapplied_payments=unapplied,
        invoices=lines,
    )
