"""One stock-cost plan per command; moves and exact snapshots are append-only."""

import json
from decimal import Decimal

from erp_cf.db import Statement, assertion, plain
from erp_cf.errors import ConflictError, NotFoundError, ValidationError
from erp_cf.money import money, quantize
from erp_cf.store import decimal_values, now, wire

ZERO = Decimal("0")


class StockBook:
    def __init__(self, plan):
        self.plan = plan
        self.rows, self.products, self.warehouses = {}, {}, {}
        self.negative = None
        self.states = {}

    async def prepare(self, product_ids, warehouse_id):
        identities = list(dict.fromkeys(str(value) for value in product_ids))
        missing = [identity for identity in identities if identity not in self.products]
        for identity, row in (
            await self.plan.repository("catalog_products").many(missing, required=False)
        ).items():
            if row["type"] != "goods" or not row["track_inventory"]:
                raise ValidationError(
                    "Stock lines must reference existing stock-tracked goods products"
                )
            self.plan.guard("catalog_products", row)
            self.products[identity] = row
        if set(identities) - self.products.keys():
            raise ValidationError(
                "Stock lines must reference existing stock-tracked goods products"
            )
        missing = [identity for identity in identities if (identity, warehouse_id) not in self.rows]
        for start in range(0, len(missing), 80):
            chunk = missing[start : start + 80]
            for identity in chunk:
                self.rows[(identity, warehouse_id)] = None
            rows = await self.plan.repository("inventory_stock").find(
                "warehouse_id=? AND product_id IN (" + ",".join("?" for _ in chunk) + ")",
                (warehouse_id, *chunk),
            )
            for row in rows:
                self.rows[(row["product_id"], warehouse_id)] = row

    async def prepare_reversals(self, originals):
        statements = []
        for start in range(0, len(originals), 80):
            identities = [row["id"] for row in originals[start : start + 80]]
            statements.append(
                Statement(
                    "SELECT move_id,before_json,after_json FROM cf_stock_move_states "
                    "WHERE move_id IN (" + ",".join("?" for _ in identities) + ")",
                    tuple(identities),
                )
            )
        if statements:
            for result in await self.plan.db.batch(statements):
                for record in result["results"]:
                    state = plain(record)
                    self.states[state["move_id"]] = state

    async def product(self, identity):
        identity = str(identity)
        if identity not in self.products:
            product = await self.plan.repository("catalog_products").get(identity, required=False)
            if not product or product["type"] != "goods" or not product["track_inventory"]:
                raise ValidationError(
                    "Stock lines must reference existing stock-tracked goods products"
                )
            self.plan.guard("catalog_products", product)
            self.products[identity] = product
        return self.products[identity]

    async def warehouse(self, identity=None):
        if identity is None:
            rows = await self.plan.repository("inventory_warehouses").find(
                order_by="is_default DESC,code",
                limit=1,
            )
            if not rows:
                raise NotFoundError("No warehouse exists — create one in settings")
            identity = rows[0]["id"]
        identity = str(identity)
        if identity not in self.warehouses:
            row = await self.plan.repository("inventory_warehouses").get(identity)
            self.plan.guard("inventory_warehouses", row)
            self.warehouses[identity] = row
        return self.warehouses[identity]

    async def row(self, product_id, warehouse_id):
        key = (str(product_id), str(warehouse_id))
        if key not in self.rows:
            self.rows[key] = await self.plan.repository("inventory_stock").first(
                "product_id=? AND warehouse_id=?",
                key,
            )
        return self.rows[key]

    async def allow_negative(self):
        if self.negative is None:
            setting = await self.plan.repository("core_settings").first("key='inventory.guard'")
            if setting:
                self.plan.guard("core_settings", setting)
            self.negative = bool(setting and setting["value"].get("allow_negative"))
        return self.negative

    async def move(
        self,
        product_id,
        warehouse_id,
        qty,
        *,
        move_type,
        unit_cost=ZERO,
        ref_type=None,
        ref_id=None,
        ref_number=None,
        reason=None,
        reverses=None,
        restore=None,
    ):
        product_id, warehouse_id = str(product_id), str(warehouse_id)
        await self.product(product_id)
        await self.warehouse(warehouse_id)
        amounts = decimal_values({"qty": qty, "unit_cost": unit_cost}, "inventory_stock_moves")
        qty, unit_cost = amounts["qty"], amounts["unit_cost"]
        if qty == 0:
            raise ValidationError("Move quantity cannot be zero after rounding")
        row = await self.row(product_id, warehouse_id)
        on_hand, avg = (row["qty_on_hand"], row["avg_cost"]) if row else (ZERO, ZERO)
        before = {"qty": on_hand, "avg": avg}
        if qty < 0 and on_hand + qty < 0 and not await self.allow_negative():
            raise ConflictError(f"Insufficient stock: {on_hand} on hand, {-qty} requested")
        cost = avg if qty < 0 else unit_cost
        if reverses:
            cost = unit_cost
        new_qty = on_hand + qty
        if restore is not None:
            avg = restore
        elif qty > 0:
            avg = (
                quantize((on_hand * avg + qty * unit_cost) / new_qty, 6)
                if new_qty > 0
                else unit_cost
            )
        elif reverses:
            # Removing a historical receipt removes its own cost, not today's
            # average. A consumed receipt may require dependent moves to be voided first.
            value = on_hand * avg + qty * unit_cost
            if new_qty == 0:
                if money(value) != 0:
                    raise ConflictError(
                        "Void dependent stock documents before reversing this receipt"
                    )
                avg = ZERO
            else:
                avg = quantize(value / new_qty, 6)
                if avg < 0:
                    raise ConflictError(
                        "Void dependent stock documents before reversing this receipt"
                    )
        changes = decimal_values(
            {"qty_on_hand": new_qty, "avg_cost": avg, "last_move_at": now()},
            "inventory_stock",
        )
        if row:
            updated = self.plan.update("inventory_stock", row, changes)
        else:
            updated = self.plan.add(
                "inventory_stock",
                {"product_id": product_id, "warehouse_id": warehouse_id, **changes},
            )
        self.rows[(product_id, warehouse_id)] = updated
        move = self.plan.add(
            "inventory_stock_moves",
            {
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "qty": qty,
                "unit_cost": cost,
                "cogs": money(-qty * cost) if qty < 0 else ZERO,
                "move_type": move_type,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "ref_number": ref_number,
                "reason": reason,
                "created_by": self.plan.actor_id,
                "reverses_move_id": reverses,
            },
        )
        self.plan.statements.append(
            Statement(
                "INSERT INTO cf_stock_move_states(move_id,command_id,before_json,after_json) "
                "VALUES (?,?,?,?)",
                (
                    move["id"],
                    self.plan.identity,
                    json.dumps(wire(before)),
                    json.dumps(wire({"qty": updated["qty_on_hand"], "avg": updated["avg_cost"]})),
                ),
            )
        )
        return move

    async def reverse(self, original, document):
        self.plan.guard("inventory_stock_moves", original)
        self.plan.statements.append(
            assertion(
                self.plan.identity,
                "NOT EXISTS(SELECT 1 FROM inventory_stock_moves "
                "WHERE org_id=? AND reverses_move_id=?)",
                (self.plan.org_id, original["id"]),
            )
        )
        row = await self.row(original["product_id"], original["warehouse_id"])
        state = self.states.get(original["id"])
        if state is None:
            state = await self.plan.db.first(
                "SELECT before_json,after_json FROM cf_stock_move_states WHERE move_id=?",
                (original["id"],),
            )
        restore = None
        if state and row:
            before, after = json.loads(state["before_json"]), json.loads(state["after_json"])
            if row["qty_on_hand"] == Decimal(after["qty"]) and row["avg_cost"] == Decimal(
                after["avg"]
            ):
                restore = Decimal(before["avg"])
        return await self.move(
            original["product_id"],
            original["warehouse_id"],
            -original["qty"],
            move_type="reversal",
            unit_cost=original["unit_cost"],
            ref_type=original["ref_type"] + "_void",
            ref_id=document["id"],
            ref_number=document["number"],
            reverses=original["id"],
            restore=restore,
        )
