"""Inventory service — the single entry point for every stock change."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.core.service import get_setting
from app.modules.inventory.models import Stock, StockMove

logger = get_logger(__name__)


async def allow_negative(session: AsyncSession, org_id: uuid.UUID) -> bool:
    guard = await get_setting(session, org_id, "inventory.guard")
    return bool(guard and guard.get("allow_negative"))


async def get_stock_row(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Stock | None:
    result = await session.scalars(
        select(Stock).where(
            Stock.org_id == org_id,
            Stock.product_id == product_id,
            Stock.warehouse_id == warehouse_id,
        )
    )
    return result.first()


async def post_move(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    qty: Decimal,
    move_type: str,
    unit_cost: Decimal = Decimal("0"),
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    ref_number: str | None = None,
    reason: str | None = None,
    actor_id: uuid.UUID | None = None,
    reverses_move_id: uuid.UUID | None = None,
) -> StockMove:
    """Post one stock move and update the derived stock row atomically.

    - Inbound (qty > 0): moving-average cost is recomputed.
    - Outbound (qty < 0): COGS = |qty| × avg_cost; avg cost unchanged.
    - Negative stock is rejected unless the `inventory.guard` setting allows it.
    Must run inside the caller's transaction; the caller commits.
    """
    qty = Decimal(str(qty))
    unit_cost = Decimal(str(unit_cost))
    if qty == 0:
        raise ConflictError("Move quantity cannot be zero")

    row = await get_stock_row(session, org_id, product_id, warehouse_id)
    on_hand = row.qty_on_hand if row else Decimal("0")
    avg = row.avg_cost if row else Decimal("0")

    if qty < 0:
        out_qty = -qty
        if on_hand < out_qty and not await allow_negative(session, org_id):
            raise ConflictError(f"Insufficient stock: {on_hand} on hand, {out_qty} requested")
        cogs = (out_qty * avg).quantize(Decimal("0.01"))
        move_cost = avg
    else:
        cogs = Decimal("0")
        move_cost = unit_cost
        new_on_hand = on_hand + qty
        if new_on_hand > 0:
            avg = ((on_hand * avg + qty * unit_cost) / new_on_hand).quantize(Decimal("0.000001"))
        else:
            avg = unit_cost

    if row is None:
        row = Stock(org_id=org_id, product_id=product_id, warehouse_id=warehouse_id)
        session.add(row)
    row.qty_on_hand = on_hand + qty
    row.avg_cost = avg
    row.last_move_at = datetime.now(UTC)

    move = StockMove(
        org_id=org_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        qty=qty,
        move_type=move_type,
        ref_type=ref_type,
        ref_id=ref_id,
        ref_number=ref_number,
        unit_cost=move_cost,
        cogs=cogs,
        reason=reason,
        created_by=actor_id,
        reverses_move_id=reverses_move_id,
    )
    session.add(move)
    await session.flush()
    return move


async def get_product_or_404(session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID):
    from app.modules.catalog.models import Product

    product = await session.get(Product, product_id)
    if not product or product.org_id != org_id:
        raise NotFoundError("Product not found")
    return product


async def stock_on_hand(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Decimal:
    row = await get_stock_row(session, org_id, product_id, warehouse_id)
    return row.qty_on_hand if row else Decimal("0")


async def warehouse_or_404(session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID):
    from app.modules.inventory.models import Warehouse

    warehouse = await session.get(Warehouse, warehouse_id)
    if not warehouse or warehouse.org_id != org_id:
        raise NotFoundError("Warehouse not found")
    return warehouse


async def default_warehouse(session: AsyncSession, org_id: uuid.UUID) -> Any:
    from app.modules.inventory.models import Warehouse

    result = await session.scalars(
        select(Warehouse).where(Warehouse.org_id == org_id, Warehouse.is_default.is_(True)).limit(1)
    )
    warehouse = result.first()
    if not warehouse:
        result = await session.scalars(select(Warehouse).limit(1))
        warehouse = result.first()
    if not warehouse:
        raise NotFoundError("No warehouse exists — create one in settings")
    return warehouse
