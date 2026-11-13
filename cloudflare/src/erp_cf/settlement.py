"""Planned invoice balances and immutable base-currency clearing effects.

The last settlement clears the actual remaining base amount. Partial effects
keep their posted values through later voids, rather than recomputing past FX
or rounding. One context carries pending balances across repeated allocations.
"""

import uuid
from decimal import Decimal

from erp_cf.db import Statement, plain
from erp_cf.errors import ConflictError, NotFoundError, ValidationError
from erp_cf.money import coefficient, from_coefficient, money

ZERO = Decimal("0.00")
SETTLEABLE = {"posted", "partial", "paid"}
INVOICES = "invoicing_invoices"
LINES = "invoicing_invoice_lines"
PAYMENTS = "invoicing_payments"
ALLOCATIONS = "invoicing_payment_allocations"


def credit(row):
    return row["invoice_type"].endswith("_credit")


def balance(row):
    return row["total"] - row["amount_paid"] - row["applied_credits"]


def settlement_status(row):
    if row["status"] in {"draft", "void"}:
        return row["status"]
    settled = row["amount_paid"] + row["applied_credits"]
    return "paid" if settled >= row["total"] else "partial" if settled > 0 else "posted"


class Settlements:
    def __init__(self, plan):
        self.plan = plan
        self.invoices, self.used = {}, {}
        self.with_lines = set()

    async def preload(self, identities):
        missing = list(
            {str(value) for value in identities if value is not None} - self.invoices.keys()
        )
        rows = await self.plan.repository(INVOICES).many(missing)
        self.invoices.update(rows)
        self.used.update({key: ZERO for key in missing})
        statements = []
        for start in range(0, len(missing), 80):
            chunk = missing[start : start + 80]
            statements.append(
                Statement(
                    "SELECT e.invoice_id,CAST(e.amount_base AS TEXT) AS amount_base "
                    "FROM cf_settlement_effects e LEFT JOIN invoicing_payments p "
                    "ON e.source_kind='payment' AND p.id=e.source_id "
                    "LEFT JOIN invoicing_invoices c ON e.source_kind='credit' AND c.id=e.source_id "
                    "WHERE e.org_id=? AND e.invoice_id IN (" + ",".join("?" for _ in chunk) + ") "
                    "AND (p.status='recorded' OR c.status IN ('posted','partial','paid'))",
                    (self.plan.org_id, *chunk),
                )
            )
        if statements:
            for result in await self.plan.db.batch(statements):
                for item in result["results"]:
                    item = plain(item)
                    self.used[item["invoice_id"]] += from_coefficient(item["amount_base"], 2)

    async def invoice(self, identity, *, lines=False):
        identity = str(identity)
        await self.preload([identity])
        row = self.invoices.get(identity)
        if row is None:
            raise NotFoundError("Invoice not found")
        if lines and identity not in self.with_lines:
            row["lines"] = await self.plan.repository(LINES).find(
                "invoice_id=?", (identity,), order_by="position,id"
            )
            self.with_lines.add(identity)
        return row

    def change(self, row, changes):
        updated = self.plan.update(INVOICES, row, changes)
        self.invoices[row["id"]] = updated
        return updated

    def settle(self, row, *, paid=None, credits=None):
        changes = {
            "amount_paid": row["amount_paid"] if paid is None else paid,
            "applied_credits": row["applied_credits"] if credits is None else credits,
        }
        merged = {**row, **changes}
        if min(changes.values()) < 0 or balance(merged) < 0:
            raise ValidationError("Settlement exceeds the invoice balance")
        changes["status"] = settlement_status(merged)
        return self.change(row, changes)

    def effect(self, row, kind, source_id, amount, purpose, *, identity=None):
        amount = money(amount)
        if amount <= 0 or amount > balance(row):
            raise ValidationError("Settlement must be positive and within the open balance")
        remaining = row["total_base"] - self.used[row["id"]]
        if remaining < 0:
            raise ConflictError("Stored base-currency settlements exceed the invoice")
        cleared = (
            remaining if amount == balance(row) else min(money(amount / row["fx_rate"]), remaining)
        )
        self.plan.statements.append(
            Statement(
                "INSERT INTO cf_settlement_effects"
                "(id,org_id,invoice_id,source_kind,source_id,purpose,amount,amount_base) "
                "VALUES (?,?,?,?,?,?,CAST(? AS INTEGER),CAST(? AS INTEGER))",
                (
                    identity or str(uuid.uuid7()),
                    self.plan.org_id,
                    row["id"],
                    kind,
                    source_id,
                    purpose,
                    coefficient(amount, 2),
                    coefficient(cleared, 2),
                ),
            )
        )
        self.used[row["id"]] += cleared
        return cleared
