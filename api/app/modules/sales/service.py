"""Sales service functions other modules may call (module boundary = service layer)."""

import uuid

from app.core.errors import ConflictError, NotFoundError
from app.modules.sales.models import SalesOrder
from app.shared.order_engine import ORDER_TRANSITIONS

ALLOWED_REMOTE = {"delivered", "invoiced", "closed"}


async def mark_status(
    session,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    status: str,  # noqa: ANN001
) -> SalesOrder:
    if status not in ALLOWED_REMOTE:
        raise ConflictError(f"Status {status} cannot be set remotely")
    order = await session.get(SalesOrder, order_id)
    if not order or order.org_id != org_id:
        raise NotFoundError("Sales order not found")
    current = order.status
    if current == status:
        return order
    if status not in ORDER_TRANSITIONS.get(current, set()):
        raise ConflictError(f"Cannot move sales order from '{current}' to '{status}'")
    order.status = status
    await session.flush()
    return order
