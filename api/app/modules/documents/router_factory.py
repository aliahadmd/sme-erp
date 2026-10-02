"""Factory for order-document routers (sales & purchasing).

One implementation of list/create/get/update/confirm/cancel so both document
types behave identically. Cross-module reads (crm parties, catalog products)
happen through runtime imports — the documents module is a base layer like
core, and business modules never import it.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.shared.events import Event, publish
from app.shared.numbering import next_number
from app.shared.order_engine import (
    apply_line_math,
    ensure_editable,
    ensure_transition,
    order_number_prefix,
    recompute_header,
    resolve_line_references,
)
from app.shared.pagination import PageParamsDep, paginate


@dataclass
class OrderModuleConfig:
    prefix: str  # url prefix e.g. "/sales"
    tags: list[str] = field(default_factory=list)
    model: Any = None  # order model class
    schemas: Any = None  # schemas module (OrderCreateIn/OrderUpdateIn/OrderOut/OrderLineIn)
    entity: str = ""  # numbering entity, e.g. "sales_order"
    number_default_prefix: str = "SO"
    party_field: str = "customer_id"
    party_snapshot_field: str = "customer_name"
    perm: str = "sales.order"
    event_base: str = "sales_order"
    label: str = "sales order"
    is_purchase: bool = False
    progress_field: str = "qty_delivered"


async def _snapshot_fx(session: AsyncSession, org_id: uuid.UUID, order: Any) -> None:
    from app.modules.currencies.service import resolve_rate, to_base

    order.fx_rate = await resolve_rate(session, org_id, order.currency, order.order_date)
    order.total_base = to_base(order.total, order.fx_rate)


def build_order_router(cfg: OrderModuleConfig) -> APIRouter:
    router = APIRouter(prefix=cfg.prefix, tags=cfg.tags)

    async def _party(session: AsyncSession, party_id: uuid.UUID):
        from app.modules.crm.models import Contact

        contact = await session.get(Contact, party_id)
        if not contact or contact.status != "active":
            raise ValidationError(f"{cfg.label} requires an active contact")
        if cfg.is_purchase and not contact.is_supplier:
            raise ValidationError("Contact is not a supplier")
        if not cfg.is_purchase and not contact.is_customer:
            raise ValidationError("Contact is not a customer")
        return contact

    async def _get_order(session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID):
        order = await session.get(cfg.model, order_id)
        if not order or order.org_id != org_id:
            raise NotFoundError(f"{cfg.label.capitalize()} not found")
        return order

    async def _build_lines(
        session: AsyncSession, org_id: uuid.UUID, line_inputs: list[Any]
    ) -> list[Any]:
        line_model = cfg.model.__mapper__.relationships["lines"].mapper.class_
        lines = []
        for position, line_in in enumerate(line_inputs):
            data = line_in.model_dump()
            data["_is_purchase"] = cfg.is_purchase
            data = await resolve_line_references(session, org_id, data)
            line = line_model(
                position=position,
                **{k: v for k, v in data.items() if not k.startswith("_")},
            )
            apply_line_math(line)
            lines.append(line)
        return lines

    @router.get("/orders", response_model=cfg.schemas.OrderPage)
    async def list_orders(
        params: PageParamsDep,
        status: str | None = Query(None),
        q: str | None = Query(None, max_length=60),
        party_id: uuid.UUID | None = None,
        _user: CurrentUser = Depends(require(f"{cfg.perm}.read")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        stmt = (
            select(cfg.model)
            .where(cfg.model.org_id == org.id)
            .order_by(cfg.model.created_at.desc())
        )
        if status:
            stmt = stmt.where(cfg.model.status == status)
        if party_id:
            stmt = stmt.where(getattr(cfg.model, cfg.party_field) == party_id)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    cfg.model.number.ilike(like),
                    getattr(cfg.model, cfg.party_snapshot_field).ilike(like),
                )
            )
        rows, total = await paginate(session, stmt, params)
        return {
            "items": [cfg.schemas.OrderOut.model_validate(r) for r in rows],
            "total": total,
            "limit": params.limit,
            "offset": params.offset,
        }

    @router.get("/orders/{order_id}/outstanding", response_model=None)
    async def order_outstanding(
        order_id: uuid.UUID,
        _user: CurrentUser = Depends(require(f"{cfg.perm}.read")),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        """Per-product outstanding (not yet processed) quantities."""

        from app.shared.order_progress import remaining_by_product

        org = await get_organization(session)
        order = await _get_order(session, org.id, order_id)
        remaining = remaining_by_product(order, cfg.progress_field)
        outstanding = {p: q for p, q in remaining.items() if q > 0}
        payload = {
            "order_id": str(order.id),
            "number": order.number,
            "outstanding": {str(p): str(q) for p, q in outstanding.items()},
        }
        return payload

    @router.get("/orders/{order_id}", response_model=cfg.schemas.OrderOut)
    async def get_order(
        order_id: uuid.UUID,
        _user: CurrentUser = Depends(require(f"{cfg.perm}.read")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        order = await _get_order(session, org.id, order_id)
        return order

    @router.post("/orders", status_code=201, response_model=cfg.schemas.OrderOut)
    async def create_order(
        body: cfg.schemas.OrderCreateIn,  # type: ignore[valid-type]
        user: CurrentUser = Depends(require(f"{cfg.perm}.create")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        party = await _party(session, getattr(body, cfg.party_field))
        if not body.lines:
            raise ValidationError(f"{cfg.label.capitalize()} needs at least one line")
        prefix = await order_number_prefix(session, org.id, cfg.entity, cfg.number_default_prefix)
        number = await next_number(session, org.id, cfg.entity, prefix)
        order = cfg.model(
            org_id=org.id,
            number=number,
            order_date=body.order_date or date.today(),
            currency=body.currency or party.currency or org.base_currency,
            notes=body.notes,
            expected_date=body.expected_date,
            created_by=user.id,
            status="draft",
            **{
                cfg.party_field: party.id,
                cfg.party_snapshot_field: party.name,
            },
        )
        lines = await _build_lines(session, org.id, body.lines)
        order.lines = lines
        recompute_header(order, lines)
        await _snapshot_fx(session, org.id, order)
        session.add(order)
        await write_audit(
            session,
            actor=user.user,
            action="create",
            entity_type=cfg.event_base,
            entity_id=order.id,
            after={"number": number, "total": str(order.total)},
        )
        await session.commit()
        await session.refresh(order)
        return order

    @router.patch("/orders/{order_id}", response_model=cfg.schemas.OrderOut)
    async def update_order(
        order_id: uuid.UUID,
        body: cfg.schemas.OrderUpdateIn,  # type: ignore[valid-type]
        user: CurrentUser = Depends(require(f"{cfg.perm}.update")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        order = await _get_order(session, org.id, order_id)
        ensure_editable(order, cfg.label)
        data = body.model_dump(exclude_unset=True)
        lines_in = data.pop("lines", None)
        party_id = data.pop(cfg.party_field, None)
        if party_id:
            party = await _party(session, party_id)
            setattr(order, cfg.party_field, party.id)
            setattr(order, cfg.party_snapshot_field, party.name)
        for field_name in ("order_date", "expected_date", "currency", "notes"):
            if field_name in data and (data[field_name] is not None or field_name != "currency"):
                setattr(order, field_name, data[field_name])
        if lines_in is not None:
            if not lines_in:
                raise ValidationError(f"{cfg.label.capitalize()} needs at least one line")
            order.lines = await _build_lines(session, org.id, list(body.lines or []))
            recompute_header(order, order.lines)
        # Currency/date/lines may all have changed: re-take the FX snapshot.
        await _snapshot_fx(session, org.id, order)
        await write_audit(
            session,
            actor=user.user,
            action="update",
            entity_type=cfg.event_base,
            entity_id=order.id,
            after={"number": order.number},
        )
        await session.commit()
        await session.refresh(order)
        return order

    @router.post("/orders/{order_id}/confirm", response_model=cfg.schemas.OrderOut)
    async def confirm_order(
        order_id: uuid.UUID,
        user: CurrentUser = Depends(require(f"{cfg.perm}.confirm")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        order = await _get_order(session, org.id, order_id)
        ensure_transition(order.status, "confirmed", cfg.label)
        if not order.lines:
            raise ValidationError("Cannot confirm a document without lines")
        order.status = "confirmed"
        order.confirmed_at = date.today()
        order.confirmed_by = user.id
        await write_audit(
            session,
            actor=user.user,
            action="confirm",
            entity_type=cfg.event_base,
            entity_id=order.id,
            after={"number": order.number, "total": str(order.total)},
        )
        await session.commit()
        await publish(
            Event(
                name=f"{cfg.event_base}.confirmed",
                payload={"order_id": str(order.id), "number": order.number},
                org_id=org.id,
            )
        )
        await session.refresh(order)
        return order

    @router.post("/orders/{order_id}/cancel", response_model=cfg.schemas.OrderOut)
    async def cancel_order(
        order_id: uuid.UUID,
        user: CurrentUser = Depends(require(f"{cfg.perm}.cancel")),
        session: AsyncSession = Depends(get_session),
    ) -> Any:
        org = await get_organization(session)
        order = await _get_order(session, org.id, order_id)
        ensure_transition(order.status, "cancelled", cfg.label)
        processed = any(
            Decimal(str(getattr(line, cfg.progress_field, 0) or 0)) > 0
            or Decimal(str(line.qty_invoiced or 0)) > 0
            for line in order.lines
        )
        if processed:
            raise ConflictError(
                f"{cfg.label.capitalize()} has goods moved or invoiced — void those documents first"
            )
        order.status = "cancelled"
        await write_audit(
            session,
            actor=user.user,
            action="cancel",
            entity_type=cfg.event_base,
            entity_id=order.id,
            after={"number": order.number},
        )
        await session.commit()
        await session.refresh(order)
        return order

    return router
