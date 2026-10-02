"""Per-line delivery/receipt/invoice progress on order documents.

Duck-typed helpers: they operate on any order object whose lines carry
`qty` plus a progress field (`qty_delivered`, `qty_received`, `qty_invoiced`).

- Stock flows (deliveries/receipts) match lines by product_id in position
  order (multiple lines of the same product are filled FIFO) and only goods
  products are ever moved.
- Invoicing is tracked per order line (`apply_line_progress`) so duplicate
  products and free-text lines are billed exactly once.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _lines(order: Any) -> list[Any]:
    return sorted(order.lines, key=lambda item: item.position)


def remaining_by_product(order: Any, progress_field: str) -> dict[uuid.UUID, Decimal]:
    """Remaining quantity per product across all lines of the order."""
    remaining: dict[uuid.UUID, Decimal] = {}
    for line in _lines(order):
        if line.product_id is None:
            continue
        left = _d(line.qty) - _d(getattr(line, progress_field, 0))
        remaining[line.product_id] = remaining.get(line.product_id, ZERO) + max(left, ZERO)
    return remaining


def remaining_by_line(order: Any, progress_field: str) -> dict[uuid.UUID, Decimal]:
    """Remaining quantity per order line (includes free-text lines)."""
    return {
        line.id: max(_d(line.qty) - _d(getattr(line, progress_field, 0)), ZERO)
        for line in _lines(order)
    }


def apply_progress(
    order: Any, progress_field: str, amounts: list[tuple[uuid.UUID, Decimal]]
) -> None:
    """Distribute processed quantities onto matching order lines (FIFO by product).

    Raises if an amount exceeds what is still outstanding — that guards
    against double-processing races between concurrent document flows.
    """
    for product_id, qty in amounts:
        left = _d(qty)
        for line in _lines(order):
            if left <= 0:
                break
            if line.product_id != product_id:
                continue
            progressed = _d(getattr(line, progress_field, 0))
            capacity = _d(line.qty) - progressed
            if capacity <= 0:
                continue
            applied = min(capacity, left)
            setattr(line, progress_field, progressed + applied)
            left -= applied
        if left > 0:
            raise ValueError(
                f"Cannot apply {left} of product {product_id}: "
                f"order line is already fully processed"
            )


def apply_line_progress(
    order: Any, progress_field: str, amounts: list[tuple[uuid.UUID, Decimal]]
) -> None:
    """Apply processed quantities to specific order lines (by line id)."""
    by_id = {line.id: line for line in order.lines}
    for line_id, qty in amounts:
        line = by_id.get(line_id)
        if line is None:
            raise ValueError(f"Order line {line_id} does not belong to order {order.number}")
        progressed = _d(getattr(line, progress_field, 0))
        if progressed + _d(qty) > _d(line.qty):
            raise ValueError(
                f"Cannot apply {qty} to line {line.position + 1} of {order.number}: "
                f"only {_d(line.qty) - progressed} outstanding"
            )
        setattr(line, progress_field, progressed + _d(qty))


def release_progress(
    order: Any, progress_field: str, amounts: list[tuple[uuid.UUID, Decimal]]
) -> None:
    """Undo processed quantities by product (voids) — newest lines first."""
    for product_id, qty in amounts:
        left = _d(qty)
        for line in reversed(_lines(order)):
            if left <= 0:
                break
            if line.product_id != product_id:
                continue
            progressed = _d(getattr(line, progress_field, 0))
            taken = min(progressed, left)
            setattr(line, progress_field, progressed - taken)
            left -= taken


def release_line_progress(
    order: Any, progress_field: str, amounts: list[tuple[uuid.UUID, Decimal]]
) -> None:
    """Undo processed quantities on specific order lines (by line id)."""
    by_id = {line.id: line for line in order.lines}
    for line_id, qty in amounts:
        line = by_id.get(line_id)
        if line is None:
            continue
        progressed = _d(getattr(line, progress_field, 0))
        setattr(line, progress_field, max(progressed - _d(qty), ZERO))


def is_fully_progressed(
    order: Any, progress_field: str, only_products: set[uuid.UUID] | None = None
) -> bool:
    """True when every relevant line is fully processed.

    `only_products` restricts the check to those products (stock flows pass
    the order's goods products — services and free text are never moved).
    Without it every line counts (invoicing).
    """
    for line in order.lines:
        if only_products is not None and line.product_id not in only_products:
            continue
        if _d(getattr(line, progress_field, 0)) < _d(line.qty):
            return False
    return True


async def goods_product_ids(session: AsyncSession, order: Any) -> set[uuid.UUID]:
    """Products on the order that move stock (goods with inventory tracking)."""
    from app.modules.catalog.models import Product

    ids = {line.product_id for line in order.lines if line.product_id is not None}
    if not ids:
        return set()
    rows = await session.scalars(
        select(Product.id).where(
            Product.id.in_(ids), Product.type == "goods", Product.track_inventory.is_(True)
        )
    )
    return set(rows)


async def derive_order_status(
    session: AsyncSession, order: Any, move_field: str, moved_status: str
) -> None:
    """Recompute an open order's status from its line progress.

    confirmed → (moved_status | invoiced) → closed. Invoicing outranks
    movement for display; closed requires BOTH complete. Draft/cancelled
    orders are never touched.
    """
    if order.status in ("draft", "cancelled"):
        return
    goods = await goods_product_ids(session, order)
    fully_moved = is_fully_progressed(order, move_field, only_products=goods)
    fully_invoiced = is_fully_progressed(order, "qty_invoiced")
    any_invoiced = any(_d(line.qty_invoiced) > 0 for line in order.lines)
    any_moved = any(_d(getattr(line, move_field, 0)) > 0 for line in order.lines)
    if fully_moved and fully_invoiced:
        order.status = "closed"
    elif any_invoiced:
        order.status = "invoiced"
    elif any_moved:
        order.status = moved_status
    else:
        order.status = "confirmed"
