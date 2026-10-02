"""Inventory API: warehouses, stock, receipts, deliveries, adjustments.

Journal entries (Inventory/GRNI on receipts, COGS on deliveries, stock
corrections) are posted by transactional subscribers (`emit` before commit),
so stock movements and their bookkeeping are saved atomically.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import and_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.catalog.models import Product
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.modules.inventory import schemas as inv
from app.modules.inventory.models import (
    Adjustment,
    AdjustmentLine,
    Delivery,
    DeliveryLine,
    Receipt,
    ReceiptLine,
    Stock,
    StockMove,
    Warehouse,
)
from app.modules.inventory.service import (
    default_warehouse,
    get_stock_row,
    post_move,
    warehouse_or_404,
)
from app.modules.purchasing import service as purchasing_service
from app.modules.sales import service as sales_service
from app.shared.events import Event, emit, publish
from app.shared.numbering import next_number
from app.shared.order_engine import order_number_prefix
from app.shared.order_progress import goods_product_ids, remaining_by_product
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/inventory")

ZERO = Decimal("0")
# Orders accept goods movements in any open state — invoicing may come first.
MOVABLE_ORDER_STATUSES = ("confirmed", "delivered", "received", "invoiced")


async def _stocked_product(session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID):
    product = await session.get(Product, product_id)
    if (
        not product
        or product.org_id != org_id
        or product.type != "goods"
        or not product.track_inventory
    ):
        raise ValidationError("Stock lines must reference existing stock-tracked goods products")
    return product


def _check_against_order(order: Any, progress_field: str, lines: list[dict]) -> None:
    """Manual lines on an order-sourced document must not exceed what the
    order still has outstanding per product."""
    remaining = remaining_by_product(order, progress_field)
    requested: dict[uuid.UUID, Decimal] = {}
    for line in lines:
        requested[line["product_id"]] = requested.get(line["product_id"], ZERO) + line["qty"]
    for product_id, qty in requested.items():
        if qty > remaining.get(product_id, ZERO):
            raise ConflictError(
                f"{qty} requested but only {remaining.get(product_id, ZERO)} outstanding "
                f"on {order.number} for this product"
            )


# ---------------------------------------------------------------- warehouses
@router.get("/warehouses", response_model=list[inv.WarehouseOut])
async def list_warehouses(
    _user: CurrentUser = Depends(require("inventory.warehouse.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Warehouse]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(Warehouse).where(Warehouse.org_id == org.id).order_by(Warehouse.code)
        )
    )


@router.post("/warehouses", response_model=inv.WarehouseOut, status_code=201)
async def create_warehouse(
    body: inv.WarehouseIn,
    user: CurrentUser = Depends(require("inventory.warehouse.create")),
    session: AsyncSession = Depends(get_session),
) -> Warehouse:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(Warehouse).where(Warehouse.org_id == org.id, Warehouse.code == body.code.upper())
        )
    ).first()
    if existing:
        raise ConflictError(f"Warehouse {body.code.upper()} already exists")
    warehouse = Warehouse(
        org_id=org.id, code=body.code.upper(), name=body.name, is_default=body.is_default
    )
    if body.is_default:
        for other in await session.scalars(select(Warehouse).where(Warehouse.org_id == org.id)):
            other.is_default = False
    session.add(warehouse)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="inventory.warehouse",
        entity_id=warehouse.id,
        after={"code": warehouse.code},
    )
    await session.commit()
    await session.refresh(warehouse)
    return warehouse


# --------------------------------------------------------------------- stock
@router.get("/stock", response_model=list[inv.StockRowOut])
async def stock_on_hand(
    warehouse_id: uuid.UUID | None = None,
    low_only: bool = False,
    _user: CurrentUser = Depends(require("inventory.stock.read")),
    session: AsyncSession = Depends(get_session),
) -> list[inv.StockRowOut]:
    org = await get_organization(session)
    if low_only:
        # Low stock must include products at ZERO and products never stocked
        # in a warehouse — those are the most urgent ones.
        stmt = (
            select(Product, Warehouse, Stock)
            .select_from(Product)
            .join(Warehouse, true())  # every stocked product × every warehouse
            .outerjoin(
                Stock,
                and_(Stock.product_id == Product.id, Stock.warehouse_id == Warehouse.id),
            )
            .where(
                Product.org_id == org.id,
                Warehouse.org_id == org.id,
                Product.status == "active",
                Product.type == "goods",
                Product.track_inventory.is_(True),
                Product.min_stock > 0,
            )
            .order_by(Product.name)
        )
        if warehouse_id:
            stmt = stmt.where(Warehouse.id == warehouse_id)
        rows = [
            (stock, product, warehouse)
            for product, warehouse, stock in (await session.execute(stmt)).all()
            if (stock.qty_on_hand if stock else ZERO) <= product.min_stock
        ]
    else:
        stmt = (
            select(Stock, Product, Warehouse)
            .join(Product, Product.id == Stock.product_id)
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .where(Stock.org_id == org.id, Stock.qty_on_hand != 0)
            .order_by(Product.name)
        )
        if warehouse_id:
            stmt = stmt.where(Stock.warehouse_id == warehouse_id)
        rows = list((await session.execute(stmt)).all())
    out = []
    for stock, product, warehouse in rows:
        qty = stock.qty_on_hand if stock else ZERO
        avg = stock.avg_cost if stock else ZERO
        out.append(
            inv.StockRowOut(
                product_id=product.id,
                product_sku=product.sku,
                product_name=product.name,
                warehouse_id=warehouse.id,
                warehouse_code=warehouse.code,
                qty_on_hand=qty,
                avg_cost=avg,
                stock_value=(qty * avg).quantize(Decimal("0.01")),
                is_low=qty <= product.min_stock and product.min_stock > 0,
            )
        )
    return out


@router.get("/moves", response_model=inv.MovePage)
async def list_moves(
    params: PageParamsDep,
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    _user: CurrentUser = Depends(require("inventory.stock.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.MovePage:
    org = await get_organization(session)
    stmt = select(StockMove).where(StockMove.org_id == org.id).order_by(StockMove.moved_at.desc())
    if product_id:
        stmt = stmt.where(StockMove.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(StockMove.warehouse_id == warehouse_id)
    rows, total = await paginate(session, stmt, params)
    return inv.MovePage(
        items=[inv.StockMoveOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


async def _reverse_moves(
    session: AsyncSession, org_id: uuid.UUID, ref_type: str, document: Any, actor_id: uuid.UUID
) -> None:
    originals = (
        await session.scalars(
            select(StockMove).where(StockMove.ref_type == ref_type, StockMove.ref_id == document.id)
        )
    ).all()
    for move in originals:
        await post_move(
            session,
            org_id=org_id,
            product_id=move.product_id,
            warehouse_id=move.warehouse_id,
            qty=-move.qty,
            move_type="reversal",
            unit_cost=move.unit_cost,
            ref_type=f"{ref_type}_void",
            ref_id=document.id,
            ref_number=document.number,
            actor_id=actor_id,
            reverses_move_id=move.id,
        )


def _stock_event(name: str, key: str, document: Any, org_id: uuid.UUID, **extra: Any) -> Event:
    return Event(
        name=name,
        payload={
            key: str(document.id),
            "document_id": str(document.id),
            "number": document.number,
            "warehouse_id": str(document.warehouse_id),
            **extra,
        },
        org_id=org_id,
    )


# ------------------------------------------------------------------ receipts
async def _receipt_or_404(
    session: AsyncSession, org_id: uuid.UUID, receipt_id: uuid.UUID
) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if not receipt or receipt.org_id != org_id:
        raise NotFoundError("Receipt not found")
    return receipt


@router.get("/receipts", response_model=inv.ReceiptPage)
async def list_receipts(
    params: PageParamsDep,
    status: str | None = None,
    _user: CurrentUser = Depends(require("inventory.receipt.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.ReceiptPage:
    org = await get_organization(session)
    stmt = select(Receipt).where(Receipt.org_id == org.id).order_by(Receipt.created_at.desc())
    if status:
        stmt = stmt.where(Receipt.status == status)
    rows, total = await paginate(session, stmt, params)
    return inv.ReceiptPage(
        items=[inv.ReceiptOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/receipts/{receipt_id}", response_model=inv.ReceiptOut)
async def get_receipt(
    receipt_id: uuid.UUID,
    _user: CurrentUser = Depends(require("inventory.receipt.read")),
    session: AsyncSession = Depends(get_session),
) -> Receipt:
    org = await get_organization(session)
    return await _receipt_or_404(session, org.id, receipt_id)


def _po_unit_cost_base(po: Any, line: Any) -> Decimal:
    """Net (after discount) PO unit price converted to the base currency —
    inventory is always valued in base currency."""
    qty = Decimal(str(line.qty))
    net_unit = Decimal(str(line.line_subtotal)) / qty if qty else Decimal(str(line.unit_price))
    return (net_unit / Decimal(str(po.fx_rate))).quantize(Decimal("0.000001"))


@router.post("/receipts", response_model=inv.ReceiptOut, status_code=201)
async def create_receipt(
    body: inv.ReceiptIn,
    user: CurrentUser = Depends(require("inventory.receipt.create")),
    session: AsyncSession = Depends(get_session),
) -> Receipt:
    org = await get_organization(session)
    warehouse_id = body.warehouse_id or (await default_warehouse(session, org.id)).id
    await warehouse_or_404(session, org.id, warehouse_id)

    source = None
    lines_data = [line.model_dump() for line in body.lines]
    if body.source_po_id:
        from app.modules.purchasing.models import PurchaseOrder

        po = await session.get(PurchaseOrder, body.source_po_id)
        if not po or po.org_id != org.id:
            raise NotFoundError("Purchase order not found")
        if po.status not in MOVABLE_ORDER_STATUSES:
            raise ConflictError(f"Cannot receive against a '{po.status}' purchase order")
        source = po
        goods = await goods_product_ids(session, po)
        if not lines_data:
            # One receipt line per outstanding PO line (FIFO-consistent).
            lines_data = [
                {
                    "product_id": line.product_id,
                    "qty": Decimal(str(line.qty)) - Decimal(str(line.qty_received)),
                    "unit_cost": _po_unit_cost_base(po, line),
                }
                for line in sorted(po.lines, key=lambda item: item.position)
                if line.product_id in goods
                and Decimal(str(line.qty)) > Decimal(str(line.qty_received))
            ]
            if not lines_data:
                raise ConflictError(f"Purchase order {po.number} is fully received")
        else:
            _check_against_order(po, "qty_received", lines_data)
    if not lines_data:
        raise ValidationError("Receipt needs at least one line (or a source order)")
    for line in lines_data:
        await _stocked_product(session, org.id, line["product_id"])

    prefix = await order_number_prefix(session, org.id, "receipt", "RCV")
    number = await next_number(session, org.id, "receipt", prefix)
    receipt = Receipt(
        org_id=org.id,
        number=number,
        warehouse_id=warehouse_id,
        source_type="purchase_order" if source else None,
        source_id=source.id if source else None,
        source_number=source.number if source else None,
        notes=body.notes,
    )
    session.add(receipt)
    await session.flush()
    for data in lines_data:
        session.add(ReceiptLine(receipt_id=receipt.id, **data))
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="inventory.receipt",
        entity_id=receipt.id,
        after={"number": number},
    )
    await session.commit()
    await session.refresh(receipt)
    return receipt


@router.post("/receipts/{receipt_id}/post", response_model=inv.ReceiptOut)
async def post_receipt(
    receipt_id: uuid.UUID,
    user: CurrentUser = Depends(require("inventory.receipt.post")),
    session: AsyncSession = Depends(get_session),
) -> Receipt:
    org = await get_organization(session)
    receipt = await _receipt_or_404(session, org.id, receipt_id)
    if receipt.status != "draft":
        raise ConflictError(f"Cannot post a receipt in status '{receipt.status}'")
    for line in receipt.lines:
        await post_move(
            session,
            org_id=org.id,
            product_id=line.product_id,
            warehouse_id=receipt.warehouse_id,
            qty=line.qty,
            move_type="receipt",
            unit_cost=line.unit_cost,
            ref_type="receipt",
            ref_id=receipt.id,
            ref_number=receipt.number,
            actor_id=user.id,
        )
    receipt.status = "posted"
    receipt.posted_at = datetime.now(UTC)
    receipt.posted_by = user.id
    if receipt.source_id:
        await purchasing_service.register_receipt(
            session, org.id, receipt.source_id, [(ln.product_id, ln.qty) for ln in receipt.lines]
        )
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="inventory.receipt",
        entity_id=receipt.id,
        after={"number": receipt.number},
    )
    event = _stock_event("receipt.posted", "receipt_id", receipt, org.id)
    await emit(session, event)  # Inventory / GRNI journal — same transaction
    await session.commit()
    await publish(event)
    await session.refresh(receipt)
    return receipt


@router.post("/receipts/{receipt_id}/void", response_model=inv.ReceiptOut)
async def void_receipt(
    receipt_id: uuid.UUID,
    user: CurrentUser = Depends(require("inventory.receipt.void")),
    session: AsyncSession = Depends(get_session),
) -> Receipt:
    org = await get_organization(session)
    receipt = await _receipt_or_404(session, org.id, receipt_id)
    if receipt.status != "posted":
        raise ConflictError("Only posted receipts can be voided")
    await _reverse_moves(session, org.id, "receipt", receipt, user.id)
    if receipt.source_id:
        await purchasing_service.release_receipt(
            session, org.id, receipt.source_id, [(ln.product_id, ln.qty) for ln in receipt.lines]
        )
    receipt.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="inventory.receipt",
        entity_id=receipt.id,
        after={"number": receipt.number},
    )
    event = _stock_event("receipt.voided", "receipt_id", receipt, org.id)
    await emit(session, event)
    await session.commit()
    await publish(event)
    await session.refresh(receipt)
    return receipt


# ---------------------------------------------------------------- deliveries
async def _delivery_or_404(
    session: AsyncSession, org_id: uuid.UUID, delivery_id: uuid.UUID
) -> Delivery:
    delivery = await session.get(Delivery, delivery_id)
    if not delivery or delivery.org_id != org_id:
        raise NotFoundError("Delivery not found")
    return delivery


@router.get("/deliveries", response_model=inv.DeliveryPage)
async def list_deliveries(
    params: PageParamsDep,
    status: str | None = None,
    _user: CurrentUser = Depends(require("inventory.delivery.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.DeliveryPage:
    org = await get_organization(session)
    stmt = select(Delivery).where(Delivery.org_id == org.id).order_by(Delivery.created_at.desc())
    if status:
        stmt = stmt.where(Delivery.status == status)
    rows, total = await paginate(session, stmt, params)
    return inv.DeliveryPage(
        items=[inv.DeliveryOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/deliveries/{delivery_id}", response_model=inv.DeliveryOut)
async def get_delivery(
    delivery_id: uuid.UUID,
    _user: CurrentUser = Depends(require("inventory.delivery.read")),
    session: AsyncSession = Depends(get_session),
) -> Delivery:
    org = await get_organization(session)
    return await _delivery_or_404(session, org.id, delivery_id)


@router.post("/deliveries", response_model=inv.DeliveryOut, status_code=201)
async def create_delivery(
    body: inv.DeliveryIn,
    user: CurrentUser = Depends(require("inventory.delivery.create")),
    session: AsyncSession = Depends(get_session),
) -> Delivery:
    org = await get_organization(session)
    warehouse_id = body.warehouse_id or (await default_warehouse(session, org.id)).id
    await warehouse_or_404(session, org.id, warehouse_id)

    source = None
    lines_data = [line.model_dump() for line in body.lines]
    if body.source_so_id:
        from app.modules.sales.models import SalesOrder

        so = await session.get(SalesOrder, body.source_so_id)
        if not so or so.org_id != org.id:
            raise NotFoundError("Sales order not found")
        if so.status not in MOVABLE_ORDER_STATUSES:
            raise ConflictError(f"Cannot deliver a '{so.status}' sales order")
        source = so
        goods = await goods_product_ids(session, so)
        if not lines_data:
            # One delivery line per outstanding goods line; services and
            # free-text lines never move stock.
            lines_data = [
                {
                    "product_id": line.product_id,
                    "qty": Decimal(str(line.qty)) - Decimal(str(line.qty_delivered)),
                }
                for line in sorted(so.lines, key=lambda item: item.position)
                if line.product_id in goods
                and Decimal(str(line.qty)) > Decimal(str(line.qty_delivered))
            ]
            if not lines_data:
                raise ConflictError(f"Sales order {so.number} is fully delivered")
        else:
            _check_against_order(so, "qty_delivered", lines_data)
    if not lines_data:
        raise ValidationError("Delivery needs at least one line (or a source order)")
    for line in lines_data:
        await _stocked_product(session, org.id, line["product_id"])

    prefix = await order_number_prefix(session, org.id, "delivery", "DLV")
    number = await next_number(session, org.id, "delivery", prefix)
    delivery = Delivery(
        org_id=org.id,
        number=number,
        warehouse_id=warehouse_id,
        source_type="sales_order" if source else None,
        source_id=source.id if source else None,
        source_number=source.number if source else None,
        notes=body.notes,
    )
    session.add(delivery)
    await session.flush()
    for data in lines_data:
        session.add(DeliveryLine(delivery_id=delivery.id, **data))
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="inventory.delivery",
        entity_id=delivery.id,
        after={"number": number},
    )
    await session.commit()
    await session.refresh(delivery)
    return delivery


@router.post("/deliveries/{delivery_id}/post", response_model=inv.DeliveryOut)
async def post_delivery(
    delivery_id: uuid.UUID,
    user: CurrentUser = Depends(require("inventory.delivery.post")),
    session: AsyncSession = Depends(get_session),
) -> Delivery:
    org = await get_organization(session)
    delivery = await _delivery_or_404(session, org.id, delivery_id)
    if delivery.status != "draft":
        raise ConflictError(f"Cannot post a delivery in status '{delivery.status}'")
    moves = []
    for line in delivery.lines:
        move = await post_move(
            session,
            org_id=org.id,
            product_id=line.product_id,
            warehouse_id=delivery.warehouse_id,
            qty=-line.qty,
            move_type="delivery",
            ref_type="delivery",
            ref_id=delivery.id,
            ref_number=delivery.number,
            actor_id=user.id,
        )
        moves.append(
            {
                "product_id": str(line.product_id),
                "qty": str(-line.qty),
                "cogs": str(move.cogs),
                "warehouse_id": str(delivery.warehouse_id),
            }
        )
    delivery.status = "posted"
    delivery.posted_at = datetime.now(UTC)
    delivery.posted_by = user.id
    if delivery.source_id:
        await sales_service.register_delivery(
            session, org.id, delivery.source_id, [(ln.product_id, ln.qty) for ln in delivery.lines]
        )
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="inventory.delivery",
        entity_id=delivery.id,
        after={"number": delivery.number},
    )
    event = _stock_event(
        "delivery.posted", "delivery_id", delivery, org.id, actor_id=str(user.id), moves=moves
    )
    await emit(session, event)  # COGS journal — same transaction
    await session.commit()
    await publish(event)
    await session.refresh(delivery)
    return delivery


@router.post("/deliveries/{delivery_id}/void", response_model=inv.DeliveryOut)
async def void_delivery(
    delivery_id: uuid.UUID,
    user: CurrentUser = Depends(require("inventory.delivery.void")),
    session: AsyncSession = Depends(get_session),
) -> Delivery:
    org = await get_organization(session)
    delivery = await _delivery_or_404(session, org.id, delivery_id)
    if delivery.status != "posted":
        raise ConflictError("Only posted deliveries can be voided")
    await _reverse_moves(session, org.id, "delivery", delivery, user.id)
    if delivery.source_id:
        await sales_service.release_delivery(
            session, org.id, delivery.source_id, [(ln.product_id, ln.qty) for ln in delivery.lines]
        )
    delivery.status = "void"
    await write_audit(
        session,
        actor=user.user,
        action="void",
        entity_type="inventory.delivery",
        entity_id=delivery.id,
        after={"number": delivery.number},
    )
    event = _stock_event("delivery.voided", "delivery_id", delivery, org.id)
    await emit(session, event)  # COGS reversal — same transaction
    await session.commit()
    await publish(event)
    await session.refresh(delivery)
    return delivery


# --------------------------------------------------------------- adjustments
async def _adjustment_or_404(
    session: AsyncSession, org_id: uuid.UUID, adjustment_id: uuid.UUID
) -> Adjustment:
    adjustment = await session.get(Adjustment, adjustment_id)
    if not adjustment or adjustment.org_id != org_id:
        raise NotFoundError("Adjustment not found")
    return adjustment


@router.get("/adjustments", response_model=inv.AdjustmentPage)
async def list_adjustments(
    params: PageParamsDep,
    _user: CurrentUser = Depends(require("inventory.adjustment.read")),
    session: AsyncSession = Depends(get_session),
) -> inv.AdjustmentPage:
    org = await get_organization(session)
    stmt = (
        select(Adjustment).where(Adjustment.org_id == org.id).order_by(Adjustment.created_at.desc())
    )
    rows, total = await paginate(session, stmt, params)
    return inv.AdjustmentPage(
        items=[inv.AdjustmentOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/adjustments", response_model=inv.AdjustmentOut, status_code=201)
async def create_adjustment(
    body: inv.AdjustmentIn,
    user: CurrentUser = Depends(require("inventory.adjustment.create")),
    session: AsyncSession = Depends(get_session),
) -> Adjustment:
    org = await get_organization(session)
    warehouse_id = body.warehouse_id or (await default_warehouse(session, org.id)).id
    await warehouse_or_404(session, org.id, warehouse_id)
    if not body.lines:
        raise ValidationError("Adjustment needs at least one line")
    for line in body.lines:
        await _stocked_product(session, org.id, line.product_id)

    prefix = await order_number_prefix(session, org.id, "adjustment", "ADJ")
    number = await next_number(session, org.id, "adjustment", prefix)
    adjustment = Adjustment(
        org_id=org.id,
        number=number,
        warehouse_id=warehouse_id,
        reason=body.reason,
        notes=body.notes,
    )
    session.add(adjustment)
    await session.flush()
    for line in body.lines:
        session.add(AdjustmentLine(adjustment_id=adjustment.id, **line.model_dump()))
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="inventory.adjustment",
        entity_id=adjustment.id,
        after={"number": number, "reason": body.reason},
    )
    await session.commit()
    await session.refresh(adjustment)
    return adjustment


@router.post("/adjustments/{adjustment_id}/post", response_model=inv.AdjustmentOut)
async def post_adjustment(
    adjustment_id: uuid.UUID,
    user: CurrentUser = Depends(require("inventory.adjustment.post")),
    session: AsyncSession = Depends(get_session),
) -> Adjustment:
    org = await get_organization(session)
    adjustment = await _adjustment_or_404(session, org.id, adjustment_id)
    if adjustment.status != "draft":
        raise ConflictError(f"Cannot post an adjustment in status '{adjustment.status}'")
    for line in adjustment.lines:
        cost = Decimal(str(line.unit_cost or 0))
        if line.qty > 0 and cost == 0:
            row = await get_stock_row(session, org.id, line.product_id, adjustment.warehouse_id)
            cost = row.avg_cost if row else ZERO
        await post_move(
            session,
            org_id=org.id,
            product_id=line.product_id,
            warehouse_id=adjustment.warehouse_id,
            qty=line.qty,
            move_type="adjustment",
            unit_cost=cost,
            ref_type="adjustment",
            ref_id=adjustment.id,
            ref_number=adjustment.number,
            reason=adjustment.reason,
            actor_id=user.id,
        )
    adjustment.status = "posted"
    adjustment.posted_at = datetime.now(UTC)
    adjustment.posted_by = user.id
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="inventory.adjustment",
        entity_id=adjustment.id,
        after={"number": adjustment.number},
    )
    event = Event(
        name="adjustment.posted",
        payload={"adjustment_id": str(adjustment.id), "number": adjustment.number},
        org_id=org.id,
    )
    await emit(session, event)  # stock-correction journal — same transaction
    await session.commit()
    await publish(event)
    await session.refresh(adjustment)
    return adjustment
