"""Idempotent in-app notices and durable email intents in their caller's batch."""

import hashlib
import json
from types import SimpleNamespace

from app.shared.notification_text import document_email, party_email

from erp_cf.business_clock import business_time
from erp_cf.db import Statement
from erp_cf.errors import ValidationError
from erp_cf.recovery_policy import mail_held
from erp_cf.settlement import SETTLEABLE, balance
from erp_cf.store import now, tables

PREFERENCES = {
    "invoice_posted": "invoice_sent",
    "payment_recorded": "payment_received",
    "invoice_overdue": "overdue_reminder",
}


def label(invoice_type):
    if invoice_type.endswith("_credit"):
        return "Credit note" if invoice_type.startswith("ar") else "Supplier credit"
    return "Invoice" if invoice_type == "ar" else "Bill"


class Notifications:
    def __init__(self, plan, env, *, event_id=None, recovery_hold=False):
        self.plan, self.env, self.event_id = plan, env, event_id
        self.recovery_hold = recovery_hold
        self.recipient_cache, self.keys, self.mail_ids = {}, set(), set()

    async def recipients(self, permission):
        if permission not in self.recipient_cache:
            table = tables()["core_users"]
            rows = await self.plan.db.all(
                "SELECT u.id,u.email,u.email_prefs,CAST(u.is_active AS TEXT) AS is_active "
                "FROM core_users u WHERE u.is_active=1 AND "
                "(u.is_superuser=1 OR EXISTS(SELECT 1 FROM core_user_roles ur "
                "JOIN core_role_permissions rp ON rp.role_id=ur.role_id "
                "JOIN core_permissions p ON p.id=rp.permission_id "
                "WHERE ur.user_id=u.id AND p.code=?)) ORDER BY u.id",
                (permission,),
            )
            self.recipient_cache[permission] = [
                {key: table.fields[key].decode(value) for key, value in row.items()} for row in rows
            ]
        return self.recipient_cache[permission]

    async def mail(
        self,
        to,
        subject,
        body,
        *,
        kind,
        source_id=None,
        dedupe,
        known_new=False,
        user_id=None,
        preference=None,
    ):
        # Validate and normalize recipients without performing DNS/network I/O.
        to = party_email(SimpleNamespace(emails=[to]))
        if not to:
            return None
        identity = hashlib.sha256(json.dumps([self.plan.org_id, dedupe, to]).encode()).hexdigest()
        if identity in self.mail_ids or (
            not known_new
            and await self.plan.db.first("SELECT id FROM cf_mail_outbox WHERE id=?", (identity,))
        ):
            return identity
        self.mail_ids.add(identity)
        mode = str(getattr(self.env, "EMAIL_MODE", "disabled"))
        if mode not in {"disabled", "test", "live"}:
            raise ValidationError("Invalid email delivery mode")
        held = await mail_held(
            self.plan,
            source_id=str(source_id) if source_id else None,
            overdue=kind == "overdue" or preference == "overdue_reminder",
            sticky=self.recovery_hold,
        )
        status = "uncertain" if held else "disabled" if mode == "disabled" else "pending"
        timestamp = now().isoformat()
        self.plan.statements.append(
            Statement(
                "INSERT INTO cf_mail_outbox(id,org_id,event_id,recipient,user_id,preference,"
                "subject,body,kind,source_id,status,created_at,completed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    self.plan.org_id,
                    self.event_id,
                    to,
                    user_id,
                    preference,
                    subject.replace("\r", " ").replace("\n", " "),
                    body,
                    kind,
                    str(source_id) if source_id else None,
                    status,
                    timestamp,
                    timestamp if status == "disabled" else None,
                ),
            )
        )
        if held:
            self.plan.statements.append(
                Statement(
                    "UPDATE cf_mail_outbox SET last_error='recovery_review_required' WHERE id=?",
                    (identity,),
                )
            )
        elif mode != "disabled":
            event = self.plan.emit("mail.delivery.requested", {"mail_id": identity})
            self.plan.statements.append(
                Statement(
                    "UPDATE cf_mail_outbox SET delivery_event_id=? WHERE id=?", (event, identity)
                )
            )
        return identity

    async def many(
        self, users, kind, title, *, body=None, payload=None, link=None, dedupe, known_new=False
    ):
        existing = (
            []
            if known_new
            else await self.plan.db.all(
                "SELECT user_id FROM cf_notification_keys "
                "WHERE org_id=? AND kind=? AND dedupe_key=?",
                (self.plan.org_id, kind, dedupe),
            )
        )
        seen = {row["user_id"] for row in existing}
        created = 0
        for user in users:
            key = (user["id"], kind, dedupe)
            if user["id"] in seen or key in self.keys:
                continue
            self.keys.add(key)
            note = self.plan.add(
                "core_notifications",
                {
                    "user_id": user["id"],
                    "type": kind,
                    "title": title[:200],
                    "body": body,
                    "payload": {**(payload or {}), "dedupe": dedupe},
                    "link": link,
                },
            )
            self.plan.statements.append(
                Statement(
                    "INSERT INTO cf_notification_keys"
                    "(org_id,user_id,kind,dedupe_key,notification_id) "
                    "VALUES (?,?,?,?,?)",
                    (self.plan.org_id, user["id"], kind, dedupe, note["id"]),
                )
            )
            pref = PREFERENCES.get(kind)
            if pref and user["email_prefs"].get(pref, True):
                await self.mail(
                    user["email"],
                    title,
                    body or title,
                    kind="staff",
                    dedupe=f"{kind}:{dedupe}",
                    known_new=True,
                    user_id=user["id"],
                    preference=pref,
                    source_id=(payload or {}).get("invoice_id")
                    or (payload or {}).get("payment_id"),
                )
            created += 1
        return created

    async def overdue(self, invoice, *, on_date=None, reminders=False):
        if (
            invoice["invoice_type"] not in {"ar", "ap"}
            or invoice["status"] not in {"posted", "partial"}
            or not invoice["due_date"]
            or invoice["due_date"] >= (on_date or business_time(self.env).date())
            or balance(invoice) <= 0
        ):
            return 0
        self.plan.guard("invoicing_invoices", invoice)
        created = await self.many(
            await self.recipients("invoicing.invoice.read"),
            "invoice_overdue",
            f"{label(invoice['invoice_type'])} {invoice['number']} is overdue",
            body=(
                f"Open balance {balance(invoice)} {invoice['currency']} since {invoice['due_date']}"
            ),
            payload={"invoice_id": invoice["id"]},
            link="/invoicing",
            dedupe=f"overdue:{invoice['id']}",
        )
        # Customer reminders have their own stable key, so a lazy notice cannot
        # consume the scheduler's one-per-document customer reminder.
        if reminders and invoice["invoice_type"] == "ar" and invoice["party_id"]:
            party = await self.plan.repository("crm_contacts").get(
                invoice["party_id"], required=False
            )
            to = party_email(SimpleNamespace(**party)) if party else None
            if to:
                await self.mail(
                    to,
                    f"Payment reminder: invoice {invoice['number']}",
                    f"Hello {invoice['party_name'] or ''},\n\n"
                    f"Invoice {invoice['number']} was due on {invoice['due_date']}. "
                    f"The open balance is {balance(invoice)} {invoice['currency']}.\n\n"
                    "If you have already paid, please disregard this reminder.",
                    kind="overdue",
                    source_id=invoice["id"],
                    dedupe=f"overdue-customer:{invoice['id']}",
                )
        return created

    async def invoice(self, payload):
        row = await self.plan.repository("invoicing_invoices").get(str(payload["invoice_id"]))
        if row["status"] not in SETTLEABLE:
            return "stale_source"
        self.plan.guard("invoicing_invoices", row)
        await self.many(
            await self.recipients("invoicing.invoice.read"),
            "invoice_posted",
            f"{label(row['invoice_type'])} {row['number']} posted",
            body=f"{row['party_name'] or ''} — total {row['total']} {row['currency']}".strip(" —"),
            payload={"invoice_id": row["id"]},
            link="/invoicing",
            dedupe=f"event:{self.event_id}",
            known_new=True,
        )
        if row["invoice_type"].startswith("ar") and row["party_id"]:
            party = await self.plan.repository("crm_contacts").get(row["party_id"], required=False)
            to = party_email(SimpleNamespace(**party)) if party else None
            if to:
                org = await self.plan.repository("core_organizations").get(self.plan.org_id)
                subject, text = document_email(SimpleNamespace(**row), org["name"])
                await self.mail(
                    to,
                    subject,
                    text,
                    kind="customer",
                    source_id=row["id"],
                    dedupe=f"invoice:{self.event_id}",
                    known_new=True,
                )
        await self.overdue(row)
        return "processed"

    async def payment(self, payload):
        row = await self.plan.repository("invoicing_payments").get(str(payload["payment_id"]))
        if row["status"] != "recorded":
            return "stale_source"
        self.plan.guard("invoicing_payments", row)
        amount = f"{row['amount']} {row['currency']}"
        await self.many(
            await self.recipients("invoicing.payment.read"),
            "payment_recorded",
            f"Payment {row['number']} recorded ({amount})",
            body="Money in" if row["direction"] == "in" else "Money out",
            payload={"payment_id": row["id"]},
            link="/invoicing/payments",
            dedupe=f"event:{self.event_id}",
            known_new=True,
        )
        return "processed"

    async def delivery(self, payload):
        row = await self.plan.repository("inventory_deliveries").get(str(payload["document_id"]))
        if row["status"] != "posted":
            return "stale_source"
        self.plan.guard("inventory_deliveries", row)
        products = await self.plan.repository("catalog_products").many(
            [m["product_id"] for m in payload["moves"]], required=False
        )
        stocks, identities = [], list(products)
        for start in range(0, len(identities), 80):
            chunk = identities[start : start + 80]
            stocks.extend(
                await self.plan.repository("inventory_stock").find(
                    "warehouse_id=? AND product_id IN (" + ",".join("?" for _ in chunk) + ")",
                    (row["warehouse_id"], *chunk),
                )
            )
        by_product = {stock["product_id"]: stock for stock in stocks}
        users = list(await self.recipients("inventory.stock.read"))
        actor_id = payload.get("actor_id")
        if actor_id and not any(user["id"] == actor_id for user in users):
            actor = await self.plan.repository("core_users").get(actor_id, required=False)
            if actor and actor["is_active"]:
                users.append(actor)
        for product in products.values():
            stock = by_product.get(product["id"])
            quantity = stock["qty_on_hand"] if stock else 0
            if not product["min_stock"] or quantity > product["min_stock"]:
                continue
            self.plan.guard("catalog_products", product)
            if stock:
                self.plan.guard("inventory_stock", stock)
            await self.many(
                users,
                "low_stock",
                f"Low stock: {product['name']}",
                body=(
                    f"{quantity} on hand (minimum {product['min_stock']}) "
                    f"after delivery {row['number']}"
                ),
                payload={"product_id": product["id"], "qty_on_hand": str(quantity)},
                link="/inventory",
                dedupe=f"delivery:{row['id']}:{product['id']}:{row['warehouse_id']}",
                known_new=True,
            )
        return "processed"
