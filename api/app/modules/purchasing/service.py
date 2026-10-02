"""Purchasing service functions other modules may call."""

from app.modules.purchasing.models import PurchaseOrder
from app.shared.order_flow import OrderFlow

_flow = OrderFlow(
    model=PurchaseOrder, move_field="qty_received", moved_status="received", label="purchase order"
)

# Receipts (inventory module) — goods in, and their voids.
register_receipt = _flow.register_movement
release_receipt = _flow.release_movement

# AP bills (invoicing module) — and their voids.
register_invoiced = _flow.register_invoiced
release_invoiced = _flow.release_invoiced
