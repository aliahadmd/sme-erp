"""Native guarded order progress shared by inventory and invoicing commands.

One instance per kind/command carries planned state for repeated updates. Its
writes join the caller's stock/document/journal batch; it never commits itself.
"""

from decimal import DecimalException
from types import SimpleNamespace

from app.shared.order_progress import (
    apply_line_progress,
    apply_progress,
    is_fully_progressed,
    release_line_progress,
    release_progress,
)

from erp_cf.documents import DocumentPlan
from erp_cf.errors import ConflictError
from erp_cf.money import coefficient, quantize

OPEN = {"confirmed", "delivered", "received", "invoiced", "closed"}


class OrderFlow:
    def __init__(self, plan, kind):
        self.plan, self.kind = plan, kind
        self.documents = DocumentPlan(plan, kind)
        self.orders = {}

    async def load(self, identity):
        identity = str(identity)
        if identity not in self.orders:
            self.orders[identity] = await self.documents.load(identity)
        return self.orders[identity]

    async def change(self, identity, field, *, by_line=(), by_product=(), release=False):
        cfg = self.kind
        if field not in {cfg.move, "qty_invoiced"}:
            raise ValueError("Unknown order progress field")
        row = await self.load(identity)
        if row["status"] not in OPEN:
            raise ConflictError(f"Cannot register progress on a '{row['status']}' {cfg.label}")
        order = SimpleNamespace(**{**row, "lines": [SimpleNamespace(**r) for r in row["lines"]]})
        try:
            for amounts, direct in [(by_line, True), (by_product, False)]:
                for key, amount in amounts:
                    amount = quantize(amount, 4)
                    coefficient(amount, 4)
                    if amount <= 0:
                        raise ValueError("Progress quantity must be positive")
                    key = str(key)
                    if release:
                        matches = [
                            line
                            for line in order.lines
                            if (line.id if direct else line.product_id) == key
                        ]
                        if not matches or sum(getattr(line, field) for line in matches) < amount:
                            raise ValueError("Cannot release more than the recorded order progress")
                        function = release_line_progress if direct else release_progress
                    else:
                        function = apply_line_progress if direct else apply_progress
                    function(order, field, [(key, amount)])
        except (ValueError, DecimalException) as exc:
            raise ConflictError(str(exc)) from exc
        goods = set()
        await self.documents.preload("catalog_products", [r["product_id"] for r in row["lines"]])
        for product_id in {r["product_id"] for r in row["lines"] if r["product_id"]}:
            product = await self.documents.reference(
                "catalog_products", product_id, "Unknown order product"
            )
            if product["type"] == "goods" and product["track_inventory"]:
                goods.add(product_id)
        fully_moved = is_fully_progressed(order, cfg.move, only_products=goods)
        fully_invoiced = is_fully_progressed(order, "qty_invoiced")
        if fully_moved and fully_invoiced:
            status = "closed"
        elif any(line.qty_invoiced > 0 for line in order.lines):
            status = "invoiced"
        elif any(getattr(line, cfg.move) > 0 for line in order.lines):
            status = "received" if cfg.purchase else "delivered"
        else:
            status = "confirmed"
        lines = []
        for old, changed in zip(row["lines"], order.lines, strict=True):
            value = getattr(changed, field)
            if value != old[field]:
                old = self.plan.update(cfg.lines, old, {field: value})
            else:
                self.plan.guard(cfg.lines, old)
            lines.append(old)
        updated = self.plan.update(cfg.table, row, {"status": status})
        updated["lines"] = lines
        self.orders[str(identity)] = updated
        return updated
