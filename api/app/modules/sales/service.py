"""Sales service functions other modules may call (module boundary = service layer)."""

from app.modules.sales.models import SalesOrder
from app.shared.order_flow import OrderFlow

_flow = OrderFlow(
    model=SalesOrder, move_field="qty_delivered", moved_status="delivered", label="sales order"
)

# Deliveries (inventory module) — goods out, and their voids.
register_delivery = _flow.register_movement
release_delivery = _flow.release_movement

# AR invoices (invoicing module) — and their voids.
register_invoiced = _flow.register_invoiced
release_invoiced = _flow.release_invoiced
