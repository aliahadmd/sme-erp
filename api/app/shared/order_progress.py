"""Per-line delivery/receipt/invoice progress on order documents.

Duck-typed helpers: they operate on any order object whose lines carry
`qty` plus a progress field (`qty_delivered`, `qty_received`, `qty_invoiced`).
Lines are matched to progress by product_id in position order (multiple lines
of the same product are filled FIFO).
"""

import uuid
from decimal import Decimal
from typing import Any


def remaining_by_product(order: Any, progress_field: str) -> dict[uuid.UUID, Decimal]:
    """Remaining quantity per product across all lines of the order."""
    remaining: dict[uuid.UUID, Decimal] = {}
    for line in sorted(order.lines, key=lambda item: item.position):
        if line.product_id is None:
            continue  # free-text lines: no product matching, always outstanding
        done = Decimal(str(getattr(line, progress_field, 0)))
        left = Decimal(str(line.qty)) - done
        current = remaining.get(line.product_id, Decimal("0"))
        remaining[line.product_id] = current + max(left, Decimal("0"))
    return remaining


def apply_progress(
    order: Any, progress_field: str, amounts: list[tuple[uuid.UUID, Decimal]]
) -> None:
    """Distribute processed quantities onto matching order lines (FIFO).

    Raises if an amount exceeds what is still outstanding — that guards
    against double-processing races between concurrent document flows.
    """
    for product_id, qty in amounts:
        left = Decimal(str(qty))
        for line in sorted(order.lines, key=lambda item: item.position):
            if left <= 0:
                break
            if line.product_id != product_id:
                continue
            capacity = Decimal(str(line.qty)) - Decimal(str(getattr(line, progress_field, 0)))
            if capacity <= 0:
                continue
            progressed = Decimal(str(getattr(line, progress_field, 0)))
            applied = min(capacity, left)
            setattr(line, progress_field, progressed + applied)
            left -= applied
        if left > 0:
            raise ValueError(
                f"Cannot apply {left} of product {product_id}: "
                f"order line is already fully processed"
            )


def is_fully_progressed(order: Any, progress_field: str) -> bool:
    for line in order.lines:
        if line.product_id is None:
            continue
        if Decimal(str(getattr(line, progress_field, 0))) < Decimal(str(line.qty)):
            return False
    return True
