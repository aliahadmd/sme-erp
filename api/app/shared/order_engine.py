"""Generic order-document engine shared by sales and purchasing.

Modules pass in their model classes; this module enforces the common rules:
- explicit state machine (documents immutable once confirmed)
- line snapshots (product name, uom, tax rate) + totals via shared/totals
- sequential numbering, audit, events (published by routers after commit)
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.shared.totals import LineMath, compute_line, compute_totals

ORDER_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"confirmed", "cancelled"},
    "confirmed": {"cancelled", "delivered", "received", "invoiced"},
    # delivered/received are set by the inventory module when goods move;
    # invoiced/closed by the invoicing module.
    "delivered": {"invoiced", "closed"},
    "received": {"invoiced", "closed"},
    "invoiced": {"closed"},
    "closed": set(),
    "cancelled": set(),
}

EDITABLE_STATUSES = {"draft"}


def ensure_transition(current: str, target: str, label: str) -> None:
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise ConflictError(f"Cannot move {label} from '{current}' to '{target}'")


def ensure_editable(order: Any, label: str) -> None:
    if order.status not in EDITABLE_STATUSES:
        raise ConflictError(f"Only draft {label}s can be edited (status: {order.status})")


def apply_line_math(line: Any) -> LineMath:
    math = compute_line(line.qty, line.unit_price, line.discount_pct, line.tax_rate_pct)
    line.line_subtotal = math.base
    line.line_tax = math.tax
    line.line_total = math.total
    return math


def recompute_header(order: Any, lines: list[Any]) -> None:
    totals = compute_totals(
        [(line.qty, line.unit_price, line.discount_pct, line.tax_rate_pct) for line in lines]
    )
    order.subtotal = totals.subtotal
    order.discount_total = totals.discount_total
    order.tax_total = totals.tax_total
    order.total = totals.total


async def resolve_line_references(
    session: AsyncSession, org_id: uuid.UUID, data: dict[str, Any]
) -> dict[str, Any]:
    """Fill snapshot fields (product name, uom code, tax rate) from catalog."""
    from app.modules.catalog.models import (  # cross-module read via owning module's tables
        Product,
        Tax,
    )

    if data.get("product_id"):
        product = await session.get(Product, data["product_id"])
        if not product or product.org_id != org_id:
            raise ValidationError("Unknown product in line")
        data["product_name"] = product.name
        if data.get("unit_price") is None:
            data["unit_price"] = (
                product.sale_price if not data.get("_is_purchase") else product.cost_price
            )
        if data.get("uom_code") is None and product.uom:
            data["uom_code"] = product.uom.code
        if data.get("tax_id") is None:
            data["tax_id"] = (
                product.sale_tax_id if not data.get("_is_purchase") else product.purchase_tax_id
            )
    if data.get("tax_id"):
        tax = await session.get(Tax, data["tax_id"])
        if not tax or tax.org_id != org_id:
            raise ValidationError("Unknown tax in line")
        data["tax_rate_pct"] = tax.rate_pct
    else:
        data["tax_rate_pct"] = Decimal("0")
    if data.get("qty") is None or Decimal(str(data["qty"])) <= 0:
        raise ValidationError("Line quantity must be positive")
    if data.get("unit_price") is None:
        raise ValidationError("Line unit price is required")
    data.pop("_is_purchase", None)
    return data


async def order_number_prefix(
    session: AsyncSession, org_id: uuid.UUID, entity: str, default: str
) -> str:
    """Prefix from settings `numbering.prefixes` (entity key), falling back to default."""
    from app.modules.core.service import get_setting

    prefixes = await get_setting(session, org_id, "numbering.prefixes")
    if prefixes and isinstance(prefixes.get(entity), str):
        return prefixes[entity]
    return default
