"""Draft/post/void invoice commands and credit-note settlement."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from app.modules.invoicing import schemas
from app.shared.notification_text import document_email, party_email
from app.shared.totals import compute_line

from erp_cf.accounting import Ledger
from erp_cf.documents import PURCHASE, SALES, DocumentPlan, today, totals
from erp_cf.errors import ConflictError, ValidationError
from erp_cf.fx import snapshot
from erp_cf.invoice_postings import InvoiceBooks
from erp_cf.money import exact_sum
from erp_cf.numbering import next_number
from erp_cf.order_flow import OrderFlow
from erp_cf.settlement import (
    INVOICES,
    LINES,
    PAYMENTS,
    SETTLEABLE,
    ZERO,
    Settlements,
    balance,
    credit,
    settlement_status,
)
from erp_cf.store import now


def order_kind(invoice_type):
    return SALES if invoice_type.startswith("ar") else PURCHASE


def copy_line(source, position, qty, *, from_order=False):
    keys = [
        "product_id",
        "product_name",
        "description",
        "uom_code",
        "unit_price",
        "discount_pct",
        "tax_id",
        "tax_rate_pct",
    ]
    row = {k: source[k] for k in keys}
    row.update(position=position, qty=qty, source_line_id=source["id"] if from_order else None)
    math = compute_line(qty, row["unit_price"], row["discount_pct"], row["tax_rate_pct"])
    return {**row, "line_subtotal": math.base, "line_tax": math.tax, "line_total": math.total}


class Invoices:
    def __init__(self, plan):
        self.plan = plan
        self.settlements = Settlements(plan)
        self.order_flows = {}

    def order_flow(self, invoice_type):
        kind = order_kind(invoice_type)
        if kind.table not in self.order_flows:
            self.order_flows[kind.table] = OrderFlow(self.plan, kind)
        return self.order_flows[kind.table]

    async def party(self, identity, invoice_type):
        row = await self.plan.repository("crm_contacts").get(str(identity), required=False)
        if row is None or row["status"] != "active":
            raise ValidationError("Invoice requires an active contact")
        if not row["is_customer" if invoice_type.startswith("ar") else "is_supplier"]:
            raise ValidationError(
                "Contact is not a customer"
                if invoice_type.startswith("ar")
                else "Contact is not a supplier"
            )
        self.plan.guard("crm_contacts", row)
        return row

    async def input_lines(self, inputs, invoice_type):
        return await DocumentPlan(
            self.plan, replace(order_kind(invoice_type), lines=LINES)
        ).build_lines(inputs)

    async def credits(self, original_id, *, excluding=None):
        rows = await self.plan.repository(INVOICES).find(
            "original_invoice_id=? AND status IN ('posted','partial','paid')", (original_id,)
        )
        return [r for r in rows if r["id"] != excluding]

    async def cap(self, original, amount, *, excluding=None):
        already = exact_sum(
            [r["total"] for r in await self.credits(original["id"], excluding=excluding)]
        )
        if already + amount > original["total"]:
            raise ConflictError(
                f"Credit exceeds the invoice total "
                f"(already credited {already} of {original['total']})"
            )

    async def source(self, row):
        cfg = order_kind(row["invoice_type"])
        source = await self.order_flow(row["invoice_type"]).load(row["source_id"])
        if source[cfg.party] != row["party_id"]:
            raise ValidationError("Invoice party must match the source order")
        if source["currency"] != row["currency"]:
            raise ValidationError("Invoices use the source order's currency")
        return source

    async def validate_links(self, row):
        if row["original_invoice_id"]:
            original = await self.settlements.invoice(row["original_invoice_id"])
            if original["invoice_type"] != row["invoice_type"].removesuffix("_credit"):
                raise ValidationError("Credit note type must match the original invoice")
            if original["party_id"] != row["party_id"] or original["currency"] != row["currency"]:
                raise ValidationError(
                    "Credit note party and currency must match the original invoice"
                )
        if row["source_id"]:
            return await self.source(row)
        return None

    async def create(self, payload):
        body = schemas.InvoiceCreateIn.model_validate(payload["body"])
        party = await self.party(body.party_id, body.invoice_type)
        data = body.model_dump(exclude={"lines", "source_order_id"})
        data.update(
            party_id=party["id"],
            party_name=party["name"],
            invoice_date=body.invoice_date or today(),
        )
        is_credit = body.invoice_type.endswith("_credit")
        if is_credit != bool(body.original_invoice_id):
            raise ValidationError("Only credit notes must reference the original invoice")
        original = None
        if is_credit:
            original = await self.settlements.invoice(body.original_invoice_id, lines=True)
            self.plan.guard(INVOICES, original)
            if original["status"] not in SETTLEABLE:
                raise ConflictError("Can only credit posted invoices")
            if (
                original["invoice_type"] != body.invoice_type.removesuffix("_credit")
                or original["party_id"] != party["id"]
            ):
                raise ValidationError("Credit note type and party must match the original invoice")
            if body.currency and body.currency != original["currency"]:
                raise ValidationError("Credit notes use the original invoice's currency")
            data["currency"] = original["currency"]
            lines = (
                await self.input_lines(body.lines, body.invoice_type)
                if body.lines
                else [copy_line(r, i, r["qty"]) for i, r in enumerate(original["lines"])]
            )
        elif body.source_order_id:
            cfg = order_kind(body.invoice_type)
            source = await DocumentPlan(self.plan, cfg).load(str(body.source_order_id))
            self.plan.guard(cfg.table, source)
            if source["status"] not in {"confirmed", "delivered", "received", "invoiced"}:
                raise ConflictError("Only confirmed/processed orders can be invoiced")
            if source[cfg.party] != party["id"]:
                raise ValidationError("Invoice party must match the source order")
            if body.currency and body.currency != source["currency"]:
                raise ValidationError("Invoices use the source order's currency")
            data.update(
                currency=source["currency"],
                source_type=cfg.entity,
                source_id=source["id"],
                source_number=source["number"],
            )
            lines = []
            for row in source["lines"]:
                self.plan.guard(cfg.lines, row)
                qty = row["qty"] - row["qty_invoiced"]
                if qty > 0:
                    lines.append(copy_line(row, len(lines), qty, from_order=True))
            if not lines:
                raise ConflictError(f"Order {source['number']} is fully invoiced")
        else:
            lines = await self.input_lines(body.lines, body.invoice_type)
        if not data.get("source_id") and any(r.get("source_line_id") for r in lines):
            raise ValidationError("Source line IDs require a source order")
        org = await self.plan.organization()
        data["currency"] = data["currency"] or party["currency"] or org["base_currency"]
        data.update(totals(lines))
        data.update(
            await snapshot(self.plan, data["currency"], data["invoice_date"], data["total"])
        )
        if original:
            await self.cap(original, data["total"])
        if data["due_date"] is None and party["payment_terms_days"] and not is_credit:
            data["due_date"] = data["invoice_date"] + timedelta(days=party["payment_terms_days"])
        row = self.plan.add(INVOICES, data)
        row["lines"] = [self.plan.add(LINES, {"invoice_id": row["id"], **line}) for line in lines]
        self.plan.audit(
            "create",
            "invoicing.invoice",
            row["id"],
            after={"party": party["name"], "total": str(row["total"]), "currency": row["currency"]},
        )
        return schemas.InvoiceOut.model_validate(row)

    async def update(self, payload):
        body = schemas.InvoiceUpdateIn.model_validate(payload["body"])
        row = await self.settlements.invoice(payload["id"], lines=True)
        if row["status"] != "draft":
            raise ConflictError("Only draft invoices can be edited")
        changes = body.model_dump(exclude_unset=True)
        replace_lines = "lines" in changes
        changes.pop("lines", None)
        if changes.get("party_id"):
            party = await self.party(body.party_id, row["invoice_type"])
            changes.update(party_id=party["id"], party_name=party["name"])
        else:
            changes.pop("party_id", None)
        if not changes.get("currency"):
            changes.pop("currency", None)
        if (
            changes.get("currency") != row["currency"]
            and changes.get("currency")
            and (row["original_invoice_id"] or row["source_id"])
        ):
            raise ValidationError("The currency follows the source document")
        if not changes.get("invoice_date"):
            changes.pop("invoice_date", None)
        merged = {**row, **changes}
        source = await self.validate_links(merged)
        lines = row["lines"]
        if replace_lines:
            lines = await self.input_lines(body.lines, row["invoice_type"])
            source_lines = {r["id"]: r for r in source["lines"]} if source else {}
            for i, line in enumerate(lines):
                if source and not line.get("source_line_id") and i < len(row["lines"]):
                    old = row["lines"][i]
                    if str(old["product_id"]) == str(line["product_id"]):
                        line["source_line_id"] = old["source_line_id"]
                if line.get("source_line_id"):
                    original = source_lines.get(str(line["source_line_id"]))
                    if original is None or str(original["product_id"]) != str(line["product_id"]):
                        raise ValidationError("Source line does not belong to this order/product")
                if not source and line.get("source_line_id"):
                    raise ValidationError("Source line IDs require a source order")
            changes.update(totals(lines))
        merged.update(changes)
        changes.update(
            await snapshot(self.plan, merged["currency"], merged["invoice_date"], merged["total"])
        )
        updated = self.settlements.change(row, changes)
        if replace_lines:
            for old in row["lines"]:
                self.plan.delete(LINES, old)
            lines = [self.plan.add(LINES, {"invoice_id": row["id"], **line}) for line in lines]
        updated["lines"] = lines
        self.plan.audit(
            "update",
            "invoicing.invoice",
            row["id"],
            after={"total": str(updated["total"]), "currency": updated["currency"]},
        )
        return schemas.InvoiceOut.model_validate(updated)

    async def progress(self, row, *, release=False):
        if not row["source_id"]:
            return
        await self.source(row)
        by_line, by_product = {}, {}
        for line in row["lines"]:
            target, key = (
                (by_line, line["source_line_id"])
                if line["source_line_id"]
                else (by_product, line["product_id"])
            )
            if key:
                target[key] = target.get(key, ZERO) + line["qty"]
        await self.order_flow(row["invoice_type"]).change(
            row["source_id"],
            "qty_invoiced",
            by_line=list(by_line.items()),
            by_product=list(by_product.items()),
            release=release,
        )

    def event(self, row, name):
        self.plan.emit(
            name,
            {
                "invoice_id": row["id"],
                "number": row["number"],
                "invoice_type": row["invoice_type"],
                "party_id": row["party_id"],
                "party_name": row["party_name"],
                "currency": row["currency"],
                "total": row["total"],
                "total_base": row["total_base"],
            },
        )

    async def post(self, payload):
        row = await self.settlements.invoice(payload["id"], lines=True)
        if row["status"] != "draft":
            raise ConflictError(f"Cannot post an invoice in status '{row['status']}'")
        if not row["lines"]:
            raise ValidationError("Cannot post an invoice without lines")
        await self.validate_links(row)
        for line in row["lines"]:
            self.plan.guard(LINES, line)
        applied, normal_base, credit_base = ZERO, ZERO, ZERO
        if credit(row):
            original = await self.settlements.invoice(row["original_invoice_id"])
            if original["status"] not in SETTLEABLE:
                raise ConflictError("Can only credit posted invoices")
            await self.cap(original, row["total"], excluding=row["id"])
            applied = min(row["total"], max(balance(original), ZERO))
            if applied:
                normal_base = self.settlements.effect(
                    original, "credit", row["id"], applied, "credit_applied"
                )
                credit_base = self.settlements.effect(
                    row, "credit", row["id"], applied, "credit_used"
                )
            # Touch the original even when already paid: the credit cap is binding.
            self.settlements.settle(original, credits=original["applied_credits"] + applied)
        entity = row["invoice_type"] if credit(row) else row["invoice_type"] + "_invoice"
        changes = {
            "number": await next_number(self.plan, entity),
            "status": "posted",
            "posted_at": now(),
            "posted_by": self.plan.actor_id,
            "amount_paid": applied,
        }
        changes["status"] = settlement_status({**row, **changes})
        updated = self.settlements.change(row, changes)
        await self.progress(updated)
        books = InvoiceBooks(self.plan)
        await books.invoice(updated)
        if applied:
            await books.credit_application(updated, normal_base, credit_base)
        self.plan.audit(
            "post",
            "invoicing.invoice",
            row["id"],
            after={"number": updated["number"], "total": str(updated["total"])},
        )
        self.event(updated, "invoice.posted")
        return schemas.InvoiceOut.model_validate(updated)

    async def void(self, payload):
        row = await self.settlements.invoice(payload["id"], lines=True)
        if row["status"] in {"draft", "void"}:
            raise ConflictError(
                "Draft invoices are deleted, and void invoices cannot be voided again"
            )
        if not credit(row):
            if row["amount_paid"] > 0:
                raise ConflictError("Invoice has payments allocated — void them first")
            if row["applied_credits"] > 0 or await self.credits(row["id"]):
                raise ConflictError("Invoice has credit notes — void them first")
        else:
            refunds = await self.plan.repository(PAYMENTS).find(
                "credit_note_id=? AND status='recorded'", (row["id"],)
            )
            if refunds:
                raise ConflictError("Credit note has refunds recorded — void them first")
            original = await self.settlements.invoice(row["original_invoice_id"])
            self.settlements.settle(
                original, credits=original["applied_credits"] - row["amount_paid"]
            )
        await self.progress(row, release=True)
        await Ledger(self.plan).reverse(row["id"], memo=f"Reversal: invoice {row['number']}")
        updated = self.settlements.change(
            row, {"status": "void", **({"amount_paid": ZERO} if credit(row) else {})}
        )
        self.plan.audit("void", "invoicing.invoice", row["id"], after={"number": row["number"]})
        self.event(updated, "invoice.voided")
        return schemas.InvoiceOut.model_validate(updated)

    async def delete(self, payload):
        row = await self.settlements.invoice(payload["id"])
        if row["status"] != "draft":
            raise ConflictError("Only draft invoices can be deleted — void posted ones")
        self.plan.audit(
            "delete",
            "invoicing.invoice",
            row["id"],
            before={"party": row["party_name"], "total": str(row["total"])},
        )
        self.plan.delete(INVOICES, row)

    async def email(self, payload):
        row = await self.settlements.invoice(payload["id"])
        if row["status"] not in SETTLEABLE or not row["invoice_type"].startswith("ar"):
            raise ValidationError("Only posted customer invoices and credit notes are emailed")
        party = (
            await self.plan.repository("crm_contacts").get(row["party_id"], required=False)
            if row["party_id"]
            else None
        )
        to = party_email(SimpleNamespace(**party)) if party else None
        if not to:
            raise ValidationError("Customer has no valid email address")
        org = await self.plan.organization()
        subject, text = document_email(SimpleNamespace(**row), org["name"])
        self.plan.guard(INVOICES, row)
        self.plan.emit(
            "email.requested", {"invoice_id": row["id"], "to": to, "subject": subject, "body": text}
        )
        self.plan.audit("email", "invoicing.invoice", row["id"], after={"to": to})
        return {"status": "queued", "to": to}
