"""Purchasing module — routers."""

from app.modules.documents.router_factory import OrderModuleConfig, build_order_router
from app.modules.purchasing import models, schemas


def build_router():
    return build_order_router(
        OrderModuleConfig(
            prefix="/purchasing",
            tags=["purchasing"],
            model=models.PurchaseOrder,
            schemas=schemas,
            entity="purchase_order",
            number_default_prefix="PO",
            party_field="supplier_id",
            party_snapshot_field="supplier_name",
            perm="purchasing.order",
            event_base="purchase_order",
            label="purchase order",
            is_purchase=True,
            progress_field="qty_received",
        )
    )


router = build_router()
