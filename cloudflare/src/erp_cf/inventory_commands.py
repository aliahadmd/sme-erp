"""Stock documents plan movements, progress, journals, audit and events together."""

from dataclasses import dataclass
from decimal import Decimal

from app.modules.inventory import schemas

from erp_cf.accounting import Ledger
from erp_cf.documents import PURCHASE, SALES, DocumentPlan, today
from erp_cf.errors import ConflictError, ValidationError
from erp_cf.money import exact_sum, money, quantize
from erp_cf.numbering import next_number
from erp_cf.order_flow import OrderFlow
from erp_cf.stock import StockBook
from erp_cf.store import decimal_values, now

ZERO = Decimal("0")


@dataclass(frozen=True)
class InventoryKind:
    entity: str
    table: str
    lines: str
    parent: str
    label: str
    permission: str
    create: type
    out: type
    order: object = None
    source: str = ""


RECEIPT = InventoryKind(
    "receipt",
    "inventory_receipts",
    "inventory_receipt_lines",
    "receipt_id",
    "receipt",
    "inventory.receipt",
    schemas.ReceiptIn,
    schemas.ReceiptOut,
    PURCHASE,
    "source_po_id",
)
DELIVERY = InventoryKind(
    "delivery",
    "inventory_deliveries",
    "inventory_delivery_lines",
    "delivery_id",
    "delivery",
    "inventory.delivery",
    schemas.DeliveryIn,
    schemas.DeliveryOut,
    SALES,
    "source_so_id",
)
ADJUSTMENT = InventoryKind(
    "adjustment",
    "inventory_adjustments",
    "inventory_adjustment_lines",
    "adjustment_id",
    "adjustment",
    "inventory.adjustment",
    schemas.AdjustmentIn,
    schemas.AdjustmentOut,
)
KINDS = [RECEIPT, DELIVERY, ADJUSTMENT]
MOVABLE = {"confirmed", "delivered", "received", "invoiced"}


async def create_warehouse(plan, actor, payload):
    body = schemas.WarehouseIn.model_validate(payload["body"])
    code = body.code.upper()
    if await plan.repository("inventory_warehouses").first("code=?", (code,)):
        raise ConflictError(f"Warehouse {code} already exists")
    if body.is_default:
        for row in await plan.repository("inventory_warehouses").find("is_default=1"):
            plan.update("inventory_warehouses", row, {"is_default": False})
    row = plan.add("inventory_warehouses", {**body.model_dump(), "code": code})
    plan.audit("create", "inventory.warehouse", row["id"], after={"code": code})
    return schemas.WarehouseOut.model_validate(row)


class InventoryPlan:
    def __init__(self, plan, kind):
        self.plan, self.kind = plan, kind
        self.stock = StockBook(plan)

    async def load(self, identity):
        row = await self.plan.repository(self.kind.table).get(identity)
        lines = await self.plan.repository(self.kind.lines).find(
            f"{self.kind.parent}=?", (str(identity),), order_by="id"
        )
        return {**row, "lines": lines}

    async def create(self, payload):
        cfg, plan = self.kind, self.plan
        body = cfg.create.model_validate(payload["body"])
        warehouse = await self.stock.warehouse(body.warehouse_id)
        source = None
        lines = [decimal_values(line.model_dump(), cfg.lines) for line in body.lines]
        source_id = getattr(body, cfg.source) if cfg.source else None
        if source_id:
            source = await DocumentPlan(plan, cfg.order).load(str(source_id))
            plan.guard(cfg.order.table, source)
            if source["status"] not in MOVABLE:
                raise ConflictError(
                    f"Cannot move goods on a '{source['status']}' {cfg.order.label}"
                )
            remaining = {}
            products = await plan.repository("catalog_products").many(
                [line["product_id"] for line in source["lines"] if line["product_id"]]
            )
            for line in source["lines"]:
                plan.guard(cfg.order.lines, line)
                if not line["product_id"]:
                    continue
                product = products[line["product_id"]]
                plan.guard("catalog_products", product)
                if product["type"] != "goods" or not product["track_inventory"]:
                    continue
                qty = max(line["qty"] - line[cfg.order.move], ZERO)
                remaining[product["id"]] = remaining.get(product["id"], ZERO) + qty
                if not body.lines and qty > 0:
                    data = {"product_id": product["id"], "qty": qty}
                    if cfg is RECEIPT:
                        data["unit_cost"] = quantize(
                            line["line_subtotal"] / line["qty"] / source["fx_rate"], 6
                        )
                    lines.append(decimal_values(data, cfg.lines))
            if not body.lines and not lines:
                raise ConflictError(
                    f"{cfg.order.label.capitalize()} {source['number']} is fully "
                    + ("received" if cfg is RECEIPT else "delivered")
                )
            requested = {}
            for line in lines:
                identity = str(line["product_id"])
                requested[identity] = requested.get(identity, ZERO) + line["qty"]
            for product_id, qty in requested.items():
                if qty > remaining.get(product_id, ZERO):
                    raise ConflictError(
                        f"{qty} requested but only {remaining.get(product_id, ZERO)} "
                        f"outstanding on {source['number']}"
                    )
        if not lines:
            raise ValidationError(
                f"{cfg.label.capitalize()} needs at least one line (or a source order)"
            )
        await self.stock.prepare([line["product_id"] for line in lines], warehouse["id"])
        for line in lines:
            await self.stock.product(line["product_id"])
            if line["qty"] == 0 or (cfg is not ADJUSTMENT and line["qty"] < 0):
                raise ValidationError("Stock quantity must be nonzero after rounding")
        data = {
            "number": await next_number(plan, cfg.entity),
            "warehouse_id": warehouse["id"],
            "notes": body.notes,
        }
        if cfg is ADJUSTMENT:
            data["reason"] = body.reason
        elif source:
            data.update(
                source_type=cfg.order.entity, source_id=source["id"], source_number=source["number"]
            )
        row = plan.add(cfg.table, data)
        row["lines"] = [plan.add(cfg.lines, {cfg.parent: row["id"], **line}) for line in lines]
        plan.audit("create", cfg.permission, row["id"], after={"number": row["number"]})
        return cfg.out.model_validate(row)

    async def journal(self, row, moves):
        cfg, plan = self.kind, self.plan
        value = money(exact_sum([r["qty"] * r["unit_cost"] for r in moves]))
        if value == 0:
            return
        book = Ledger(plan)
        inventory = await book.account("inventory")
        if cfg is RECEIPT:
            lines = [(inventory, value, ZERO), (await book.account("grni"), ZERO, value)]
        elif cfg is DELIVERY:
            lines = [(await book.account("cogs"), -value, ZERO), (inventory, ZERO, -value)]
        else:
            correction = await book.account("stock_correction")
            lines = (
                [(inventory, value, ZERO), (correction, ZERO, value)]
                if value > 0
                else [(correction, -value, ZERO), (inventory, ZERO, -value)]
            )
        await book.post(
            entry_date=today(),
            memo=f"{cfg.label.capitalize()} {row['number']}",
            source_type=cfg.entity,
            source_id=row["id"],
            lines=lines,
        )

    async def post(self, payload):
        cfg, plan = self.kind, self.plan
        row = await self.load(payload["id"])
        if row["status"] != "draft":
            raise ConflictError(f"Cannot post a {cfg.label} in status '{row['status']}'")
        if not row["lines"]:
            raise ValidationError("Cannot post a stock document without lines")
        await self.stock.prepare([line["product_id"] for line in row["lines"]], row["warehouse_id"])
        moves = []
        for line in row["lines"]:
            plan.guard(cfg.lines, line)
            cost = line["unit_cost"]
            if cfg is ADJUSTMENT and line["qty"] > 0 and cost == 0:
                current = await self.stock.row(line["product_id"], row["warehouse_id"])
                cost = current["avg_cost"] if current else ZERO
            moves.append(
                await self.stock.move(
                    line["product_id"],
                    row["warehouse_id"],
                    -line["qty"] if cfg is DELIVERY else line["qty"],
                    move_type=cfg.entity,
                    unit_cost=cost,
                    ref_type=cfg.entity,
                    ref_id=row["id"],
                    ref_number=row["number"],
                    reason=row.get("reason"),
                )
            )
        if cfg.order and row["source_id"]:
            await OrderFlow(plan, cfg.order).change(
                row["source_id"],
                cfg.order.move,
                by_product=[(ln["product_id"], ln["qty"]) for ln in row["lines"]],
            )
        updated = plan.update(
            cfg.table, row, {"status": "posted", "posted_at": now(), "posted_by": plan.actor_id}
        )
        updated["lines"] = row["lines"]
        await self.journal(updated, moves)
        plan.audit("post", cfg.permission, row["id"], after={"number": row["number"]})
        plan.emit(
            f"{cfg.entity}.posted",
            {
                f"{cfg.entity}_id": row["id"],
                "document_id": row["id"],
                "number": row["number"],
                "warehouse_id": row["warehouse_id"],
                "actor_id": plan.actor_id,
                "moves": [
                    {
                        "product_id": r["product_id"],
                        "warehouse_id": r["warehouse_id"],
                        "qty": r["qty"],
                        "cogs": r["cogs"],
                    }
                    for r in moves
                ],
            },
        )
        return cfg.out.model_validate(updated)

    async def void(self, payload):
        cfg, plan = self.kind, self.plan
        row = await self.load(payload["id"])
        if row["status"] != "posted":
            raise ConflictError(f"Only posted {cfg.label}s can be voided")
        originals = await plan.repository("inventory_stock_moves").find(
            "ref_type=? AND ref_id=?", (cfg.entity, row["id"]), order_by="moved_at DESC,id DESC"
        )
        if not originals:
            raise ConflictError("Posted document has no stock movements")
        await self.stock.prepare([move["product_id"] for move in originals], row["warehouse_id"])
        await self.stock.prepare_reversals(originals)
        for move in originals:
            await self.stock.reverse(move, row)
        if row.get("source_id") and cfg.order:
            await OrderFlow(plan, cfg.order).change(
                row["source_id"],
                cfg.order.move,
                by_product=[(ln["product_id"], ln["qty"]) for ln in row["lines"]],
                release=True,
            )
        await Ledger(plan).reverse(row["id"], memo=f"Reversal: {row['number']}")
        updated = plan.update(cfg.table, row, {"status": "void"})
        updated["lines"] = row["lines"]
        plan.audit("void", cfg.permission, row["id"], after={"number": row["number"]})
        plan.emit(
            f"{cfg.entity}.voided",
            {
                f"{cfg.entity}_id": row["id"],
                "document_id": row["id"],
                "number": row["number"],
                "warehouse_id": row["warehouse_id"],
            },
        )
        return cfg.out.model_validate(updated)


def command(kind, action):
    async def run(plan, actor, payload):
        return await getattr(InventoryPlan(plan, kind), action)(payload)

    return run


COMMANDS = {"inventory.warehouse.create": ("inventory.warehouse.create", 201, create_warehouse)}
for kind in KINDS:
    for action in ["create", "post"] if kind is ADJUSTMENT else ["create", "post", "void"]:
        COMMANDS[f"{kind.permission}.{action}"] = (
            f"{kind.permission}.{action}",
            201 if action == "create" else 200,
            command(kind, action),
        )
