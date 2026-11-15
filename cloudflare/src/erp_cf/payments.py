"""Atomic payments, on-account allocations, credit refunds and exact voids."""

from app.modules.invoicing import schemas

from erp_cf.accounting import Ledger
from erp_cf.documents import today
from erp_cf.errors import ConflictError, NotFoundError, ValidationError
from erp_cf.fx import snapshot
from erp_cf.invoice_postings import InvoiceBooks
from erp_cf.money import exact_sum, money
from erp_cf.numbering import next_number
from erp_cf.settlement import ALLOCATIONS, PAYMENTS, Settlements, balance, credit
from erp_cf.store import decimal_values, now


class Payments:
    def __init__(self, plan):
        self.plan, self.settlements = plan, Settlements(plan)

    async def load(self, identity):
        row = await self.plan.repository(PAYMENTS).get(str(identity))
        row["allocations"] = await self.plan.repository(ALLOCATIONS).find(
            "payment_id=?", (row["id"],), order_by="id"
        )
        return row

    @staticmethod
    def amounts(values):
        result = []
        for item in values:
            amount = decimal_values({"amount": item.amount}, ALLOCATIONS)["amount"]
            if amount <= 0:
                raise ValidationError("Allocation must be positive after rounding")
            result.append((str(item.invoice_id), amount))
        return result

    async def preload(self, identities):
        try:
            await self.settlements.preload(identities)
        except NotFoundError as exc:
            raise ValidationError("Unknown invoice in allocations") from exc

    async def allocate(self, payment, amounts):
        await self.preload([identity for identity, _ in amounts])
        result = []
        expected = "ar" if payment["direction"] == "in" else "ap"
        for identity, amount in amounts:
            invoice = await self.settlements.invoice(identity)
            if invoice["invoice_type"] != expected:
                raise ValidationError(
                    f"Invoice {invoice['number']} is not an {expected.upper()} invoice"
                )
            if invoice["party_id"] != payment["party_id"]:
                raise ValidationError(f"Invoice {invoice['number']} belongs to a different party")
            if invoice["currency"] != payment["currency"]:
                raise ValidationError("Invoice and payment currencies must match")
            if invoice["status"] not in {"posted", "partial"}:
                raise ValidationError(f"Invoice {invoice['number']} is not open for payment")
            if amount > balance(invoice):
                raise ValidationError(
                    f"Allocation {amount} exceeds open balance {balance(invoice)} "
                    f"on {invoice['number']}"
                )
            allocation = self.plan.add(
                ALLOCATIONS, {"payment_id": payment["id"], "invoice_id": identity, "amount": amount}
            )
            cleared = self.settlements.effect(
                invoice, "payment", payment["id"], amount, "allocation", identity=allocation["id"]
            )
            updated = self.settlements.settle(invoice, paid=invoice["amount_paid"] + amount)
            result.append((allocation, cleared, updated))
        return result

    def event(self, payment, name):
        self.plan.emit(
            name,
            {
                key: payment[key]
                for key in [
                    "number",
                    "direction",
                    "party_id",
                    "currency",
                    "amount",
                    "amount_base",
                    "method",
                    "credit_note_id",
                ]
            }
            | {"payment_id": payment["id"]},
        )

    async def create(self, payload):
        body = schemas.PaymentIn.model_validate(payload["body"])
        amount = decimal_values({"amount": body.amount}, PAYMENTS)["amount"]
        if amount <= 0:
            raise ValidationError("Payment must be positive after rounding")
        amounts = self.amounts(body.allocations)
        total_allocated = exact_sum([value for _, value in amounts])
        if total_allocated > amount:
            raise ValidationError("Allocations exceed the payment amount")
        if body.credit_note_id and amounts:
            raise ValidationError("A refund cannot also be allocated to invoices")
        party = await self.plan.repository("crm_contacts").get(str(body.party_id), required=False)
        if not party or party["status"] != "active":
            raise ValidationError("Invoice requires an active contact")
        self.plan.guard("crm_contacts", party)
        await self.preload([identity for identity, _ in amounts])
        currencies = {self.settlements.invoices[identity]["currency"] for identity, _ in amounts}
        refund = None
        if body.credit_note_id:
            refund = await self.settlements.invoice(body.credit_note_id)
            if not credit(refund) or refund["status"] not in {"posted", "partial"}:
                raise ValidationError("Refunds must reference an open posted credit note")
            if refund["party_id"] != party["id"]:
                raise ValidationError("Refund party must match the credit note")
            expected = "out" if refund["invoice_type"] == "ar_credit" else "in"
            if body.direction != expected:
                raise ValidationError(
                    f"Refunds of {refund['invoice_type']} credit notes are money '{expected}'"
                )
            if amount > balance(refund):
                raise ValidationError(
                    f"Refund exceeds the credit note's open amount {balance(refund)}"
                )
            currencies.add(refund["currency"])
        if body.currency:
            currencies.add(body.currency)
        if len(currencies) > 1:
            raise ValidationError("A payment and its documents must share one currency")
        org = await self.plan.organization()
        currency = (
            next(iter(currencies)) if currencies else party["currency"] or org["base_currency"]
        )
        date = body.payment_date or today()
        rate = await snapshot(self.plan, currency, date, amount)
        payment = self.plan.add(
            PAYMENTS,
            {
                "number": await next_number(
                    self.plan, "customer_payment" if body.direction == "in" else "supplier_payment"
                ),
                "direction": body.direction,
                "party_id": party["id"],
                "party_name": party["name"],
                "payment_date": date,
                "currency": currency,
                "fx_rate": rate["fx_rate"],
                "amount": amount,
                "amount_base": rate["total_base"],
                "method": body.method,
                "reference": body.reference,
                "notes": body.notes,
                "created_by": self.plan.actor_id,
                "credit_note_id": body.credit_note_id,
            },
        )
        if refund:
            cleared = self.settlements.effect(refund, "payment", payment["id"], amount, "refund")
            self.settlements.settle(refund, paid=refund["amount_paid"] + amount)
            allocated = []
        else:
            allocated = await self.allocate(payment, amounts)
            # Allocate rounding across the payment's basis once. The remainder
            # stays on account, and later allocations consume that exact basis.
            unallocated_base = payment["amount_base"] - money(total_allocated / payment["fx_rate"])
            cleared = exact_sum([value for _, value, _ in allocated]) + unallocated_base
        payment["allocations"] = [row for row, _, _ in allocated]
        await InvoiceBooks(self.plan).payment(payment, cleared, refund=bool(refund))
        self.plan.audit(
            "create",
            "invoicing.payment",
            payment["id"],
            after={"number": payment["number"], "amount": str(amount), "currency": currency},
        )
        self.event(payment, "payment.recorded")
        return schemas.PaymentOut.model_validate(payment)

    async def allocate_existing(self, payload):
        body = schemas.PaymentAllocationsIn.model_validate(payload["body"])
        payment = await self.load(payload["id"])
        if payment["status"] != "recorded":
            raise ConflictError("Only recorded payments can be allocated")
        if payment["credit_note_id"]:
            raise ConflictError("Refunds cannot be allocated to invoices")
        values = self.amounts(body.allocations)
        previous = exact_sum([row["amount"] for row in payment["allocations"]])
        requested = exact_sum([amount for _, amount in values])
        if requested > payment["amount"] - previous:
            raise ValidationError(
                f"Only {payment['amount'] - previous} of {payment['number']} is unallocated"
            )
        # Even without a changed business field, bump the payment revision to
        # serialize competing claims on its on-account amount.
        updated = self.plan.update(PAYMENTS, payment, {})
        allocations = await self.allocate(updated, values)
        books = InvoiceBooks(self.plan)
        for allocation, cleared, invoice in allocations:
            payment_basis = money((previous + allocation["amount"]) / payment["fx_rate"]) - money(
                previous / payment["fx_rate"]
            )
            previous += allocation["amount"]
            await books.allocation(updated, invoice, cleared, payment_basis)
            self.plan.emit(
                "payment.allocated",
                {
                    "payment_id": payment["id"],
                    "invoice_id": invoice["id"],
                    "amount": allocation["amount"],
                },
            )
        updated["allocations"] = payment["allocations"] + [row for row, _, _ in allocations]
        self.plan.audit(
            "allocate",
            "invoicing.payment",
            payment["id"],
            after={"allocations": [[identity, str(value)] for identity, value in values]},
        )
        return schemas.PaymentOut.model_validate(updated)

    async def void(self, payload):
        payment = await self.load(payload["id"])
        if payment["status"] != "recorded":
            raise ConflictError("Payment is already void")
        await self.preload(
            [row["invoice_id"] for row in payment["allocations"]]
            + ([payment["credit_note_id"]] if payment["credit_note_id"] else [])
        )
        for allocation in payment["allocations"]:
            invoice = await self.settlements.invoice(allocation["invoice_id"])
            self.settlements.settle(invoice, paid=invoice["amount_paid"] - allocation["amount"])
        if payment["credit_note_id"]:
            invoice = await self.settlements.invoice(payment["credit_note_id"])
            self.settlements.settle(invoice, paid=invoice["amount_paid"] - payment["amount"])
        await Ledger(self.plan).reverse(
            payment["id"], memo=f"Reversal: payment {payment['number']}"
        )
        updated = self.plan.update(PAYMENTS, payment, {"status": "void", "voided_at": now()})
        self.plan.audit(
            "void", "invoicing.payment", payment["id"], after={"number": payment["number"]}
        )
        self.event(updated, "payment.voided")
        return schemas.PaymentOut.model_validate(updated)
