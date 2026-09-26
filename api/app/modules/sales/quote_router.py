"""Quotations API — priced offers that convert into sales orders."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.modules.sales.models import Quotation
from app.modules.sales.quote_service import (
    check_expiry,
    ensure_editable,
    ensure_transition,
    recompute,
)
from app.shared.numbering import next_number
from app.shared.order_engine import (
    apply_line_math,
    order_number_prefix,
    resolve_line_references,
)
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/sales/quotations", tags=["sales"])


class QuoteLineIn(BaseModel):
    product_id: uuid.UUID | None = None
    description: str | None = None
    qty: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(None, ge=0)
    discount_pct: Decimal = Field(0, ge=0, le=100)
    tax_id: uuid.UUID | None = None


class QuoteCreateIn(BaseModel):
    customer_id: uuid.UUID
    quote_date: date | None = None
    valid_until: date | None = None
    currency: str = Field("USD", min_length=3, max_length=3)
    notes: str | None = None
    lines: list[QuoteLineIn] = []


class QuoteUpdateIn(BaseModel):
    customer_id: uuid.UUID | None = None
    quote_date: date | None = None
    valid_until: date | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    notes: str | None = None
    lines: list[QuoteLineIn] | None = None


class QuoteLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    product_id: uuid.UUID | None
    product_name: str | None
    description: str | None
    qty: Decimal
    uom_code: str | None
    unit_price: Decimal
    discount_pct: Decimal
    tax_id: uuid.UUID | None
    tax_rate_pct: Decimal
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal


class QuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    customer_id: uuid.UUID | None
    customer_name: str | None
    quote_date: date
    valid_until: date | None
    currency: str
    status: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    notes: str | None
    created_by: uuid.UUID | None
    sent_at: date | None
    accepted_at: date | None
    converted_order_id: uuid.UUID | None
    created_at: datetime | None = None
    lines: list[QuoteLineOut] = []


async def _quote_or_404(session: AsyncSession, org_id: uuid.UUID, quote_id: uuid.UUID) -> Quotation:
    quotation = await session.get(Quotation, quote_id)
    if not quotation or quotation.org_id != org_id:
        raise NotFoundError("Quotation not found")
    return quotation


async def _party(session: AsyncSession, customer_id: uuid.UUID):
    from app.modules.crm.models import Contact

    contact = await session.get(Contact, customer_id)
    if not contact or contact.status != "active" or not contact.is_customer:
        raise ValidationError("Quotation requires an active customer contact")
    return contact


async def _build_lines(session: AsyncSession, org_id: uuid.UUID, line_inputs: list[Any]) -> list:
    from app.modules.sales.models import QuotationLine

    lines = []
    for position, line_in in enumerate(line_inputs):
        data = line_in.model_dump()
        data["_is_purchase"] = False
        data = await resolve_line_references(session, org_id, data)
        line = QuotationLine(
            position=position,
            **{k: v for k, v in data.items() if not k.startswith("_")},
        )
        apply_line_math(line)
        lines.append(line)
    return lines


@router.get("")
async def list_quotations(
    params: PageParamsDep,
    status: str | None = Query(None),
    q: str | None = Query(None, max_length=60),
    _user: CurrentUser = Depends(require("sales.quote.read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org = await get_organization(session)
    stmt = select(Quotation).where(Quotation.org_id == org.id).order_by(Quotation.created_at.desc())
    if status:
        stmt = stmt.where(Quotation.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Quotation.number.ilike(like), Quotation.customer_name.ilike(like)))
    rows, total = await paginate(session, stmt, params)
    return {
        "items": [QuoteOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get("/{quote_id}")
async def get_quotation(
    quote_id: uuid.UUID,
    _user: CurrentUser = Depends(require("sales.quote.read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org = await get_organization(session)
    quote = await _quote_or_404(session, org.id, quote_id)
    return QuoteOut.model_validate(quote).model_dump(mode="json")


@router.post("", status_code=201)
async def create_quotation(
    body: QuoteCreateIn,
    user: CurrentUser = Depends(require("sales.quote.create")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org = await get_organization(session)
    party = await _party(session, body.customer_id)
    if not body.lines:
        raise ValidationError("Quotation needs at least one line")
    prefix = await order_number_prefix(session, org.id, "quotation", "QT")
    number = await next_number(session, org.id, "quotation", prefix)
    quotation = Quotation(
        org_id=org.id,
        number=number,
        customer_id=party.id,
        customer_name=party.name,
        quote_date=body.quote_date or date.today(),
        valid_until=body.valid_until,
        currency=body.currency,
        notes=body.notes,
        created_by=user.id,
        status="draft",
    )
    lines = await _build_lines(session, org.id, body.lines)
    quotation.lines = lines
    recompute(quotation)
    from app.modules.currencies.service import resolve_rate, to_base

    quotation.fx_rate = await resolve_rate(session, org.id, body.currency, quotation.quote_date)
    quotation.total_base = to_base(quotation.total, quotation.fx_rate)
    session.add(quotation)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="sales.quotation",
        entity_id=quotation.id,
        after={"number": number, "total": str(quotation.total)},
    )
    await session.commit()
    await session.refresh(quotation)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


@router.patch("/{quote_id}")
async def update_quotation(
    quote_id: uuid.UUID,
    body: QuoteUpdateIn,
    user: CurrentUser = Depends(require("sales.quote.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org = await get_organization(session)
    quotation = await _quote_or_404(session, org.id, quote_id)
    ensure_editable(quotation)
    data = body.model_dump(exclude_unset=True)
    if "customer_id" in data and data["customer_id"]:
        party = await _party(session, data["customer_id"])
        quotation.customer_id = party.id
        quotation.customer_name = party.name
    for f in ("quote_date", "valid_until", "currency", "notes"):
        if f in data:
            setattr(quotation, f, data[f])
    if data.get("lines") is not None:
        if not body.lines:
            raise ValidationError("Quotation needs at least one line")
        quotation.lines = await _build_lines(session, org.id, list(body.lines))
        recompute(quotation)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="sales.quotation",
        entity_id=quotation.id,
        after={"number": quotation.number},
    )
    await session.commit()
    await session.refresh(quotation)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


async def _transition(
    quote_id: uuid.UUID,
    target: str,
    action: str,
    user: CurrentUser,
    session: AsyncSession,
    *,
    set_dates: dict | None = None,
) -> Quotation:
    org = await get_organization(session)
    quotation = await _quote_or_404(session, org.id, quote_id)
    check_expiry(quotation, date.today())
    ensure_transition(quotation.status, target)
    quotation.status = target
    if target == "sent":
        quotation.sent_at = date.today()
    if target == "accepted":
        quotation.accepted_at = date.today()
    if set_dates:
        for k, v in set_dates.items():
            setattr(quotation, k, v)
    await write_audit(
        session,
        actor=user.user,
        action=action,
        entity_type="sales.quotation",
        entity_id=quotation.id,
        after={"number": quotation.number, "status": target},
    )
    await session.commit()
    await session.refresh(quotation)
    return quotation


@router.post("/{quote_id}/send")
async def send_quotation(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(require("sales.quote.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    quotation = await _transition(quote_id, "sent", "send", user, session)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


@router.post("/{quote_id}/accept")
async def accept_quotation(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(require("sales.quote.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    quotation = await _transition(quote_id, "accepted", "accept", user, session)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


@router.post("/{quote_id}/reject")
async def reject_quotation(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(require("sales.quote.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    quotation = await _transition(quote_id, "rejected", "reject", user, session)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


@router.post("/{quote_id}/cancel")
async def cancel_quotation(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(require("sales.quote.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    quotation = await _transition(quote_id, "cancelled", "cancel", user, session)
    return QuoteOut.model_validate(quotation).model_dump(mode="json")


@router.post("/{quote_id}/convert")
async def convert_quotation(
    quote_id: uuid.UUID,
    user: CurrentUser = Depends(require("sales.order.create")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accepted quotation → draft sales order (exactly once)."""
    from app.modules.sales.models import SalesOrder, SalesOrderLine

    org = await get_organization(session)
    quotation = await _quote_or_404(session, org.id, quote_id)
    check_expiry(quotation, date.today())
    ensure_transition(quotation.status, "converted")
    if not quotation.lines:
        raise ValidationError("Quotation has no lines to convert")

    prefix = await order_number_prefix(session, org.id, "sales_order", "SO")
    from app.shared.numbering import next_number

    number = await next_number(session, org.id, "sales_order", prefix)
    order = SalesOrder(
        org_id=org.id,
        number=number,
        customer_id=quotation.customer_id,
        customer_name=quotation.customer_name,
        order_date=date.today(),
        currency=quotation.currency,
        fx_rate=quotation.fx_rate,
        notes=f"From {quotation.number}" + (f" — {quotation.notes}" if quotation.notes else ""),
        created_by=user.id,
        status="draft",
    )
    for position, line in enumerate(quotation.lines):
        order.lines.append(
            SalesOrderLine(
                position=position,
                product_id=line.product_id,
                product_name=line.product_name,
                description=line.description,
                qty=line.qty,
                uom_code=line.uom_code,
                unit_price=line.unit_price,
                discount_pct=line.discount_pct,
                tax_id=line.tax_id,
                tax_rate_pct=line.tax_rate_pct,
                line_subtotal=line.line_subtotal,
                line_tax=line.line_tax,
                line_total=line.line_total,
            )
        )
    from app.modules.currencies.service import to_base
    from app.shared.order_engine import recompute_header

    recompute_header(order, order.lines)
    order.total_base = to_base(order.total, order.fx_rate)
    session.add(order)
    await session.flush()
    quotation.status = "converted"
    quotation.converted_order_id = order.id
    await write_audit(
        session,
        actor=user.user,
        action="convert",
        entity_type="sales.quotation",
        entity_id=quotation.id,
        after={"number": quotation.number, "order": number},
    )
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="sales.order",
        entity_id=order.id,
        after={"number": number, "from_quotation": quotation.number},
    )
    await session.commit()
    await session.refresh(order)
    return {
        "order_id": str(order.id),
        "order_number": order.number,
        "quotation_number": quotation.number,
    }
