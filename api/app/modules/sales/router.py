"""Sales module — routers."""

from app.modules.documents.router_factory import OrderModuleConfig, build_order_router
from app.modules.sales import models, schemas


def build_router():
    return build_order_router(
        OrderModuleConfig(
            prefix="/sales",
            tags=["sales"],
            model=models.SalesOrder,
            schemas=schemas,
            entity="sales_order",
            number_default_prefix="SO",
            party_field="customer_id",
            party_snapshot_field="customer_name",
            perm="sales.order",
            event_base="sales_order",
            label="sales order",
            is_purchase=False,
        )
    )


router = build_router()
