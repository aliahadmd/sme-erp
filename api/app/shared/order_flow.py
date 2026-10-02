"""Order fulfilment/invoicing registration shared by sales and purchasing.

The owning module (sales / purchasing) exposes these through its service so
other modules (inventory, invoicing) never touch order tables directly.
Status is always derived from line progress (`derive_order_status`), so
movements and invoicing may happen in any order (bill before receipt,
invoice before delivery, partial both ways) and voids roll back cleanly.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.shared.order_progress import (
    apply_line_progress,
    apply_progress,
    derive_order_status,
    release_line_progress,
    release_progress,
)

OPEN_STATUSES = ("confirmed", "delivered", "received", "invoiced", "closed")

Amounts = list[tuple[uuid.UUID, Decimal]]


@dataclass(frozen=True)
class OrderFlow:
    model: Any
    move_field: str  # qty_delivered | qty_received
    moved_status: str  # delivered | received
    label: str

    async def _locked(self, session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID) -> Any:
        # Lock the order row: concurrent documents against one order must
        # serialize or their progress read-modify-writes race.
        order = (
            await session.scalars(
                select(self.model).where(self.model.id == order_id).with_for_update()
            )
        ).first()
        if not order or order.org_id != org_id:
            raise ConflictError(f"{self.label.capitalize()} not found")
        return order

    async def register_movement(
        self, session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, amounts: Amounts
    ) -> Any:
        order = await self._locked(session, org_id, order_id)
        if order.status not in OPEN_STATUSES:
            raise ConflictError(f"Cannot register goods on a '{order.status}' {self.label}")
        try:
            apply_progress(order, self.move_field, amounts)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        await derive_order_status(session, order, self.move_field, self.moved_status)
        await session.flush()
        return order

    async def release_movement(
        self, session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, amounts: Amounts
    ) -> Any:
        order = await self._locked(session, org_id, order_id)
        release_progress(order, self.move_field, amounts)
        await derive_order_status(session, order, self.move_field, self.moved_status)
        await session.flush()
        return order

    async def register_invoiced(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        order_id: uuid.UUID,
        *,
        by_line: Amounts,
        by_product: Amounts,
    ) -> Any:
        """Lines copied from the order carry their source line id (`by_line`);
        manually added lines fall back to product matching (`by_product`)."""
        order = await self._locked(session, org_id, order_id)
        if order.status not in OPEN_STATUSES:
            raise ConflictError(f"Cannot register invoicing on a '{order.status}' {self.label}")
        try:
            apply_line_progress(order, "qty_invoiced", by_line)
            apply_progress(order, "qty_invoiced", by_product)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        await derive_order_status(session, order, self.move_field, self.moved_status)
        await session.flush()
        return order

    async def release_invoiced(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        order_id: uuid.UUID,
        *,
        by_line: Amounts,
        by_product: Amounts,
    ) -> Any:
        order = await self._locked(session, org_id, order_id)
        release_line_progress(order, "qty_invoiced", by_line)
        release_progress(order, "qty_invoiced", by_product)
        await derive_order_status(session, order, self.move_field, self.moved_status)
        await session.flush()
        return order
