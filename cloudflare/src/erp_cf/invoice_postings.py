"""Base-currency journal recipes operating on explicit planned aggregates."""

from erp_cf.accounting import Ledger
from erp_cf.documents import today
from erp_cf.money import exact_sum, money
from erp_cf.settlement import ZERO, credit


class InvoiceBooks:
    def __init__(self, plan):
        self.plan, self.ledger = plan, Ledger(plan)

    async def fx(self, lines, difference):
        if difference > 0:
            lines.append((await self.ledger.account("fx_gain"), ZERO, difference))
        elif difference < 0:
            lines.append((await self.ledger.account("fx_loss"), -difference, ZERO))

    async def post(self, *, lines, **metadata):
        if any(debit != 0 or credit != 0 for _, debit, credit in lines):
            return await self.ledger.post(lines=lines, **metadata)
        return None

    async def invoice(self, invoice):
        book, rate, total = self.ledger, invoice["fx_rate"], invoice["total_base"]
        is_ar, is_credit = invoice["invoice_type"].startswith("ar"), credit(invoice)
        if is_ar:
            ar, revenue, tax_account = [
                await book.account(key) for key in ["ar", "sales_revenue", "tax_payable"]
            ]
            tax = money(invoice["tax_total"] / rate)
            net = total - tax
            lines = (
                [(ar, ZERO, total), (revenue, net, ZERO), (tax_account, tax, ZERO)]
                if is_credit
                else [(ar, total, ZERO), (revenue, ZERO, net), (tax_account, ZERO, tax)]
            )
        else:
            ap, purchases = [await book.account(key) for key in ["ap", "purchases"]]
            if is_credit:
                lines = [(ap, total, ZERO), (purchases, ZERO, total)]
            else:
                products = await self.plan.repository("catalog_products").many(
                    [r["product_id"] for r in invoice["lines"] if r["product_id"]], required=False
                )
                for product in products.values():
                    self.plan.guard("catalog_products", product)
                goods = exact_sum(
                    [
                        r["line_subtotal"]
                        for r in invoice["lines"]
                        if r["product_id"] in products
                        and products[r["product_id"]]["type"] == "goods"
                        and products[r["product_id"]]["track_inventory"]
                    ]
                )
                goods = min(money(goods / rate), total)
                lines = [(ap, ZERO, total), (purchases, total - goods, ZERO)]
                if goods:
                    lines.append((await book.account("grni"), goods, ZERO))
        await self.post(
            lines=lines,
            entry_date=invoice["invoice_date"],
            memo=f"{'Credit note' if is_credit else 'Invoice'} {invoice['number']}",
            source_type="ar_invoice" if is_ar else "ap_invoice",
            source_id=invoice["id"],
        )

    async def credit_application(self, invoice, original_base, credit_base):
        delta = original_base - credit_base
        if delta == 0:
            return
        is_ar = invoice["invoice_type"].startswith("ar")
        party = await self.ledger.account("ar" if is_ar else "ap")
        if is_ar:
            lines = [(party, ZERO, delta)] if delta > 0 else [(party, -delta, ZERO)]
            await self.fx(lines, -delta)
        else:
            lines = [(party, delta, ZERO)] if delta > 0 else [(party, ZERO, -delta)]
            await self.fx(lines, delta)
        await self.post(
            lines=lines,
            entry_date=invoice["invoice_date"],
            memo=f"FX on credit application {invoice['number']}",
            source_type="ar_invoice" if is_ar else "ap_invoice",
            source_id=invoice["id"],
        )

    async def payment(self, payment, cleared, *, refund=False):
        book, amount = self.ledger, payment["amount_base"]
        incoming = payment["direction"] == "in"
        cash = await book.payment_account(payment["method"])
        key = ("ap" if incoming else "ar") if refund else ("ar" if incoming else "ap")
        party = await book.account(key)
        lines = (
            [(cash, amount, ZERO), (party, ZERO, cleared)]
            if incoming
            else [(party, cleared, ZERO), (cash, ZERO, amount)]
        )
        await self.fx(lines, amount - cleared if incoming else cleared - amount)
        await self.post(
            lines=lines,
            entry_date=payment["payment_date"],
            memo=f"Payment {payment['number']}",
            source_type="payment",
            source_id=payment["id"],
        )

    async def allocation(self, payment, invoice, cleared, payment_basis):
        delta = cleared - payment_basis
        if delta == 0:
            return
        incoming = payment["direction"] == "in"
        party = await self.ledger.account("ar" if incoming else "ap")
        lines = (
            [(party, ZERO, delta)]
            if incoming and delta > 0
            else [(party, -delta, ZERO)]
            if incoming
            else [(party, delta, ZERO)]
            if delta > 0
            else [(party, ZERO, -delta)]
        )
        await self.fx(lines, -delta if incoming else delta)
        await self.post(
            lines=lines,
            entry_date=today(),
            memo=f"FX on allocation of {payment['number']} to {invoice['number']}",
            source_type="payment",
            source_id=payment["id"],
        )
