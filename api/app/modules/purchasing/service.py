"""Purchasing service functions other modules may call."""

import uuid
from decimal import Decimal

from app.core.errors import ConflictError, NotFoundError
from app.modules.purchasing.models import PurchaseOrder
from app.shared.order_engine import ORDER_TRANSITIONS

ALLOWED_REMOTE = {"received", "invoiced", "closed"}


async def register_receipt(
    session,
    org_id: uuid.UUID,
    order_id: uuid.UUID,  # noqa: ANN001
    amounts: list[tuple[uuid.UUID, Decimal]],
) -> PurchaseOrder:
    """Apply received quantities onto order lines; moves the order to
    `received`, and to `closed` once receipt AND invoicing are complete."""
    from decimal import Decimal

    from app.shared.order_progress import apply_progress, is_fully_progressed

    order = await session.get(PurchaseOrder, order_id)
    if not order or order.org_id != org_id:
        raise ConflictError("Purchase order not found")
    if order.status not in ("confirmed", "received", "closed"):
        raise ConflictError(f"Cannot register receipt on a '{order.status}' order")
    if order.status == "closed":
        return order
    try:
        apply_progress(order, "qty_received", [(p, Decimal(str(q))) for p, q in amounts])
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    if order.status == "confirmed":
        order.status = "received"
    if is_fully_progressed(order, "qty_received") and is_fully_progressed(order, "qty_invoiced"):
        order.status = "closed"
    await session.flush()
    return order


async def register_invoiced(
    session,
    org_id: uuid.UUID,
    order_id: uuid.UUID,  # noqa: ANN001
    amounts: list[tuple[uuid.UUID, Decimal]],
) -> PurchaseOrder:
    """Apply invoiced quantities; moves the order to `invoiced`, then to
    `closed` when invoicing AND receipt are complete."""
    from decimal import Decimal

    from app.shared.order_progress import apply_progress, is_fully_progressed

    order = await session.get(PurchaseOrder, order_id)
    if not order or order.org_id != org_id:
        raise ConflictError("Purchase order not found")
    if order.status not in ("confirmed", "received", "invoiced"):
        raise ConflictError(f"Cannot register invoicing on a '{order.status}' order")
    if order.status == "closed":
        return order
    try:
        apply_progress(order, "qty_invoiced", [(p, Decimal(str(q))) for p, q in amounts])
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    if order.status in ("confirmed", "received"):
        order.status = "invoiced"
    if is_fully_progressed(order, "qty_received") and is_fully_progressed(order, "qty_invoiced"):
        order.status = "closed"
    await session.flush()
    return order


async def mark_status(
    session,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    status: str,  # noqa: ANN001
) -> PurchaseOrder:
    if status not in ALLOWED_REMOTE:
        raise ConflictError(f"Status {status} cannot be set remotely")
    order = await session.get(PurchaseOrder, order_id)
    if not order or order.org_id != org_id:
        raise NotFoundError("Purchase order not found")
    current = order.status
    if current == status:
        return order
    if status not in ORDER_TRANSITIONS.get(current, set()):
        raise ConflictError(f"Cannot move purchase order from '{current}' to '{status}'")
    order.status = status
    await session.flush()
    return order
