"""Inventory API: warehouses, stock, receipts, deliveries, adjustments."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
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
from app.modules.inventory.service import default_warehouse, post_move, warehouse_or_404
from app.modules.purchasing import service as purchasing_service
from app.modules.sales import service as sales_service
from app.shared.events import Event, publish
from app.shared.numbering import next_number
from app.shared.order_engine import order_number_prefix
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/inventory")


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
    stmt = (
        select(Stock, Product, Warehouse)
        .join(Product, Product.id == Stock.product_id)
        .join(Warehouse, Warehouse.id == Stock.warehouse_id)
        .where(Stock.org_id == org.id, Stock.qty_on_hand != 0)
        .order_by(Product.name)
    )
    if warehouse_id:
        stmt = stmt.where(Stock.warehouse_id == warehouse_id)
    rows = (await session.execute(stmt)).all()
    out = []
    for stock, product, warehouse in rows:
        if low_only and stock.qty_on_hand >= product.min_stock:
            continue
        out.append(
            inv.StockRowOut(
                product_id=product.id,
                product_sku=product.sku,
                product_name=product.name,
                warehouse_id=warehouse.id,
                warehouse_code=warehouse.code,
                qty_on_hand=stock.qty_on_hand,
                avg_cost=stock.avg_cost,
                stock_value=(stock.qty_on_hand * stock.avg_cost).quantize(Decimal("0.01")),
                is_low=stock.qty_on_hand <= product.min_stock,
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
        if po.status not in ("confirmed", "received"):
            raise ConflictError("Only confirmed purchase orders can be received")
        source = po
        if not lines_data:
            lines_data = [
                {"product_id": line.product_id, "qty": line.qty, "unit_cost": line.unit_price}
                for line in po.lines
            ]
    if not lines_data:
        raise ValidationError("Receipt needs at least one line (or a source order)")
    for line in lines_data:
        product = await session.get(Product, line["product_id"])
        if not product or product.org_id != org.id or product.type != "goods":
            raise ValidationError("Receipt lines must reference existing goods products")

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
        await purchasing_service.mark_status(session, org.id, receipt.source_id, "received")
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="inventory.receipt",
        entity_id=receipt.id,
        after={"number": receipt.number},
    )
    await session.commit()
    await publish(
        Event(
            name="receipt.posted",
            payload={
                "receipt_id": str(receipt.id),
                "number": receipt.number,
                "warehouse_id": str(receipt.warehouse_id),
                "moves": [
                    {
                        "product_id": str(line.product_id),
                        "qty": str(line.qty),
                        "unit_cost": str(line.unit_cost),
                    }
                    for line in receipt.lines
                ],
            },
            org_id=org.id,
        )
    )
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
    originals = (
        await session.scalars(
            select(StockMove).where(StockMove.ref_type == "receipt", StockMove.ref_id == receipt.id)
        )
    ).all()
    for move in originals:
        await post_move(
            session,
            org_id=org.id,
            product_id=move.product_id,
            warehouse_id=move.warehouse_id,
            qty=-move.qty,
            move_type="reversal",
            unit_cost=move.unit_cost,
            ref_type="receipt_void",
            ref_id=receipt.id,
            ref_number=receipt.number,
            actor_id=user.id,
            reverses_move_id=move.id,
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
    await session.commit()
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
        if so.status not in ("confirmed", "delivered"):
            raise ConflictError("Only confirmed sales orders can be delivered")
        source = so
        if not lines_data:
            lines_data = [{"product_id": line.product_id, "qty": line.qty} for line in so.lines]
    if not lines_data:
        raise ValidationError("Delivery needs at least one line (or a source order)")
    for line in lines_data:
        product = await session.get(Product, line["product_id"])
        if not product or product.org_id != org.id or product.type != "goods":
            raise ValidationError("Delivery lines must reference existing goods products")

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
    cogs_moves = []
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
        cogs_moves.append(
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
        await sales_service.mark_status(session, org.id, delivery.source_id, "delivered")
    await write_audit(
        session,
        actor=user.user,
        action="post",
        entity_type="inventory.delivery",
        entity_id=delivery.id,
        after={"number": delivery.number},
    )
    await session.commit()
    await publish(
        Event(
            name="delivery.posted",
            payload={
                "delivery_id": str(delivery.id),
                "number": delivery.number,
                "warehouse_id": str(delivery.warehouse_id),
                "actor_id": str(user.id),
                "moves": cogs_moves,
            },
            org_id=org.id,
        )
    )
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
    originals = (
        await session.scalars(
            select(StockMove).where(
                StockMove.ref_type == "delivery", StockMove.ref_id == delivery.id
            )
        )
    ).all()
    for move in originals:
        await post_move(
            session,
            org_id=org.id,
            product_id=move.product_id,
            warehouse_id=move.warehouse_id,
            qty=-move.qty,
            move_type="reversal",
            unit_cost=move.unit_cost,
            ref_type="delivery_void",
            ref_id=delivery.id,
            ref_number=delivery.number,
            actor_id=user.id,
            reverses_move_id=move.id,
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
    await session.commit()
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
    posted_moves: list[StockMove] = []
    for line in adjustment.lines:
        stock_row = await session.scalars(
            select(Stock).where(
                Stock.org_id == org.id,
                Stock.product_id == line.product_id,
                Stock.warehouse_id == adjustment.warehouse_id,
            )
        )
        row = stock_row.first()
        cost = row.avg_cost if row else Decimal("0")
        move = await post_move(
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
        posted_moves.append(move)
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
    await session.commit()
    total_value = sum((m.qty * m.unit_cost for m in posted_moves), Decimal("0")).quantize(
        Decimal("0.01")
    )
    await publish(
        Event(
            name="adjustment.posted",
            payload={
                "adjustment_id": str(adjustment.id),
                "number": adjustment.number,
                "qty": str(sum((m.qty for m in posted_moves), Decimal("0"))),
                "value": str(total_value),
            },
            org_id=org.id,
        )
    )
    await session.refresh(adjustment)
    return adjustment
