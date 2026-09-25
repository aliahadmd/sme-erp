"""Purchasing service functions other modules may call."""

import uuid

from app.core.errors import ConflictError, NotFoundError
from app.shared.order_engine import ORDER_TRANSITIONS
from app.modules.purchasing.models import PurchaseOrder

ALLOWED_REMOTE = {"received", "invoiced", "closed"}


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
