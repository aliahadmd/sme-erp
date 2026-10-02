"""Event subscribers that turn business events into notifications.

After-commit handlers (see `app.shared.events.publish`): they open their own
session and COMMIT — the publisher's transaction is already closed. In-app
notifications are always created first; emails are queued afterwards so a
mail problem can never swallow the in-app notice.

Recipients are resolved by PERMISSION, not role names: every active user
holding the listed permission code (plus superusers) gets the notification.
Users receive an email copy when their matching email preference is on.
Customer-facing emails (posted invoices, overdue reminders) go to the
party's first valid email address and only for customer documents.
"""

import uuid
from datetime import date
from decimal import Decimal

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import or_, select

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.modules.core.models import (
    Notification,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.shared.events import Event, subscribe

logger = get_logger(__name__)

# notification type → user email preference key (see /api/email-preferences)
EMAIL_PREF_BY_TYPE = {
    "invoice_posted": "invoice_sent",
    "payment_recorded": "payment_received",
    "invoice_overdue": "overdue_reminder",
}


def party_email(contact) -> str | None:  # noqa: ANN001
    """First syntactically valid email of a contact (no header injection:
    CR/LF or malformed values are rejected by the validator)."""
    if contact is None or not contact.emails:
        return None
    for entry in contact.emails:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if not isinstance(value, str):
            continue
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized
        except EmailNotValidError:
            continue
    return None


def document_email(invoice, company: str) -> tuple[str, str]:  # noqa: ANN001
    """(subject, body) for a customer invoice / credit note email."""
    label = "Credit note" if invoice.invoice_type.endswith("_credit") else "Invoice"
    subject = f"{label} {invoice.number} from {company} — {invoice.total} {invoice.currency}"
    due = f"\nDue date: {invoice.due_date}" if invoice.due_date else ""
    body = (
        f"Hello {invoice.party_name or ''},\n\n"
        f"{label} {invoice.number} dated {invoice.invoice_date} "
        f"for {invoice.total} {invoice.currency} has been issued.{due}\n\n"
        f"Kind regards,\n{company}"
    )
    return subject, body


async def _recipients_with(session, permission_code: str) -> list[uuid.UUID]:  # noqa: ANN001
    by_role = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(User.is_active.is_(True), Permission.code == permission_code)
    )
    supers = select(User.id).where(User.is_active.is_(True), User.is_superuser.is_(True))
    result = await session.scalars(
        select(User.id).where(or_(User.id.in_(by_role), User.id.in_(supers)))
    )
    return list(result)


async def _queue_email(to: str, subject: str, body: str) -> None:
    from app.jobs.queue import enqueue

    try:
        await enqueue("send_email", to=to, subject=subject, body=body)
    except Exception:
        logger.exception("email_enqueue_failed", to=to, subject=subject)


async def _notify_many(
    session,
    user_ids: list[uuid.UUID],  # noqa: ANN001
    *,
    type_: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> int:
    """One notification per user. With dedupe_key, duplicates of the same
    type + payload.dedupe are suppressed (used by the overdue check).
    Returns the number created; queues opted-in email copies after commit."""
    from app.modules.core.service import notify

    if dedupe_key is not None:
        existing = await session.scalars(
            select(Notification.id).where(
                Notification.type == type_,
                Notification.payload["dedupe"].astext == dedupe_key,
            )
        )
        if existing.first() is not None:
            return 0

    count = 0
    for user_id in user_ids:
        final_payload = dict(payload or {})
        if dedupe_key:
            final_payload["dedupe"] = dedupe_key
        await notify(
            session,
            user_id=user_id,
            type_=type_,
            title=title,
            body=body,
            link=link,
            payload=final_payload,
        )
        count += 1
    pref = EMAIL_PREF_BY_TYPE.get(type_)
    if pref and user_ids:
        users = (await session.scalars(select(User).where(User.id.in_(user_ids)))).all()
        pending = session.info.setdefault("pending_emails", [])
        for user in users:
            if (user.email_prefs or {}).get(pref, True):
                pending.append((user.email, title, body or title))
    return count


async def _flush_emails(session) -> None:  # noqa: ANN001
    for to, subject, body in session.info.pop("pending_emails", []):
        await _queue_email(to, subject, body)


async def _on_delivery_posted(event: Event) -> None:
    """Low-stock watchdog: after goods leave, flag products at/below min stock."""
    actor_id = event.payload.get("actor_id")
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        from app.modules.catalog.models import Product
        from app.modules.inventory.service import get_stock_row

        for move in event.payload.get("moves", []):
            product_id = move.get("product_id")
            if not product_id:
                continue
            product = await session.get(Product, uuid.UUID(product_id))
            if not product or not product.min_stock:
                continue
            stock = await get_stock_row(
                session, org_id, uuid.UUID(product_id), uuid.UUID(move["warehouse_id"])
            )
            on_hand = stock.qty_on_hand if stock else Decimal("0")
            if on_hand > product.min_stock:
                continue
            recipients = await _recipients_with(session, "inventory.stock.read")
            if actor_id:
                actor_uuid = uuid.UUID(actor_id)
                if actor_uuid not in recipients:
                    recipients.append(actor_uuid)
            await _notify_many(
                session,
                recipients,
                type_="low_stock",
                title=f"Low stock: {product.name}",
                body=(
                    f"{on_hand} on hand (minimum {product.min_stock}) "
                    f"after delivery {event.payload.get('number', '')}".strip()
                ),
                link="/inventory",
                payload={"product_id": product_id, "qty_on_hand": str(on_hand)},
                dedupe_key=(
                    f"low_stock:{product_id}:{int(on_hand)}:{event.payload.get('number', '')}"
                ),
            )
        await session.commit()
        await _flush_emails(session)
    logger.info("low_stock_notifications_processed")


def _document_label(invoice_type: str) -> str:
    if invoice_type.endswith("_credit"):
        return "Credit note" if invoice_type.startswith("ar") else "Supplier credit"
    return "Invoice" if invoice_type == "ar" else "Bill"


async def _on_invoice_posted(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        recipients = await _recipients_with(session, "invoicing.invoice.read")
        label = _document_label(event.payload["invoice_type"])
        amount = f"{event.payload['total']} {event.payload.get('currency', '')}".strip()
        await _notify_many(
            session,
            recipients,
            type_="invoice_posted",
            title=f"{label} {event.payload['number']} posted",
            body=f"{event.payload.get('party_name') or ''} — total {amount}".strip(" —"),
            link="/invoicing",
            payload={"invoice_id": event.payload["invoice_id"]},
        )
        await session.commit()
        await _flush_emails(session)

        # Customer copy — only customer documents, only to a valid address.
        if event.payload["invoice_type"].startswith("ar"):
            from app.modules.core.service import get_organization
            from app.modules.crm.models import Contact
            from app.modules.invoicing.models import Invoice

            invoice = await session.get(Invoice, uuid.UUID(event.payload["invoice_id"]))
            party = await session.get(Contact, invoice.party_id) if invoice.party_id else None
            to = party_email(party)
            if to:
                org = await get_organization(session)
                subject, body = document_email(invoice, org.name)
                await _queue_email(to, subject, body)


async def _on_payment_recorded(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        recipients = await _recipients_with(session, "invoicing.payment.read")
        direction = "in" if event.payload["direction"] == "in" else "out"
        amount = f"{event.payload['amount']} {event.payload.get('currency', '')}".strip()
        await _notify_many(
            session,
            recipients,
            type_="payment_recorded",
            title=f"Payment {event.payload['number']} recorded ({amount})",
            body="Money in" if direction == "in" else "Money out",
            link="/invoicing/payments",
            payload={"payment_id": event.payload["payment_id"]},
        )
        await session.commit()
        await _flush_emails(session)


async def notify_overdue_invoices(
    session,
    org_id: uuid.UUID,
    *,
    send_reminders: bool = False,  # noqa: ANN001
) -> int:
    """One notification per overdue invoice/bill, deduped forever per document.

    Credit notes are never "overdue". With `send_reminders` (the scheduled
    job) the customer also gets one reminder email per overdue AR invoice.
    Emails are queued by the caller via `flush_overdue_emails` after commit.
    """
    from app.modules.crm.models import Contact
    from app.modules.invoicing.models import Invoice
    from app.modules.invoicing.service import open_balance

    today = date.today()
    overdue = (
        await session.scalars(
            select(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.invoice_type.in_(("ar", "ap")),
                Invoice.status.in_(("posted", "partial")),
                Invoice.due_date.is_not(None),
                Invoice.due_date < today,
            )
        )
    ).all()
    if not overdue:
        return 0
    recipients = await _recipients_with(session, "invoicing.invoice.read")
    created = 0
    for invoice in overdue:
        balance = open_balance(invoice)
        if balance <= 0:
            continue
        label = _document_label(invoice.invoice_type)
        count = await _notify_many(
            session,
            recipients,
            type_="invoice_overdue",
            title=f"{label} {invoice.number} is overdue",
            body=f"Open balance {balance} {invoice.currency} since {invoice.due_date}",
            link="/invoicing",
            payload={"invoice_id": str(invoice.id)},
            dedupe_key=f"overdue:{invoice.id}",
        )
        created += count
        if count and send_reminders and invoice.invoice_type == "ar":
            party = await session.get(Contact, invoice.party_id) if invoice.party_id else None
            to = party_email(party)
            if to:
                session.info.setdefault("pending_emails", []).append(
                    (
                        to,
                        f"Payment reminder: invoice {invoice.number}",
                        (
                            f"Hello {invoice.party_name or ''},\n\n"
                            f"Invoice {invoice.number} was due on {invoice.due_date}. "
                            f"The open balance is {balance} {invoice.currency}.\n\n"
                            "If you have already paid, please disregard this reminder."
                        ),
                    )
                )
    return created


async def flush_overdue_emails(session) -> None:  # noqa: ANN001
    """Queue the emails collected by `notify_overdue_invoices` (call after commit)."""
    await _flush_emails(session)


_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    subscribe("delivery.posted", _on_delivery_posted)
    subscribe("invoice.posted", _on_invoice_posted)
    subscribe("payment.recorded", _on_payment_recorded)
    logger.info("notification_subscribers_registered")
