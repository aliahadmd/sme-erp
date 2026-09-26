"""Event subscribers that turn business events into in-app notifications.

Mirrors the accounting postings pattern: handlers open their own session
(events fire after the publishing transaction committed) and must COMMIT —
the publisher's transaction is already closed by the time they run.

Recipients are resolved by PERMISSION, not role names: every active user
holding the listed permission code gets the notification. Overdue-invoice
notices are produced lazily (deduped per invoice) when clients poll the
unread count — phase 1 has no scheduler.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

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


async def _recipients_with(session, permission_code: str) -> list[uuid.UUID]:  # noqa: ANN001
    result = await session.scalars(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(User.is_active.is_(True), Permission.code == permission_code)
        .distinct()
    )
    return list(result)


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
    type + payload.dedupe are suppressed (used by the lazy overdue check)."""
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
    return count


async def _on_delivery_posted(event: Event) -> None:
    """Low-stock watchdog: after goods leave, flag products at/below min stock."""
    actor_id = event.payload.get("actor_id")
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        for move in event.payload.get("moves", []):
            product_id = move.get("product_id")
            if not product_id:
                continue
            from app.modules.catalog.models import Product
            from app.modules.inventory.service import get_stock_row

            product = await session.get(Product, uuid.UUID(product_id))
            if not product:
                continue
            stock = await get_stock_row(
                session, org_id, uuid.UUID(product_id), uuid.UUID(move["warehouse_id"])
            )
            on_hand = stock.qty_on_hand if stock else None
            if on_hand is None or on_hand > product.min_stock:
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
    logger.info("low_stock_notifications_processed")


async def _queue_party_email(event: Event, subject: str, body: str) -> None:
    """Queue a customer-facing email using the party's first email address."""
    from app.core.db import SessionFactory
    from app.jobs.queue import enqueue
    from app.modules.crm.models import Contact

    party_id = event.payload.get("party_id")
    if not party_id:
        return
    async with SessionFactory() as session:
        contact = await session.get(Contact, uuid.UUID(party_id))
        if not contact or not contact.emails:
            return
        to = contact.emails[0].get("value") if isinstance(contact.emails[0], dict) else None
        if not to:
            return
    await enqueue("send_email", to=to, subject=subject, body=body)


async def _on_invoice_posted(event: Event) -> None:
    label = "Credit note" if "credit" in event.payload["invoice_type"] else "Invoice"
    await _queue_party_email(
        event,
        subject=f"{label} {event.payload['number']} — {event.payload['total']}",
        body="Please find your invoice attached.",
    )
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        recipients = await _recipients_with(session, "invoicing.invoice.read")
        label = "Invoice" if event.payload["invoice_type"] == "ar" else "Bill"
        await _notify_many(
            session,
            recipients,
            type_="invoice_posted",
            title=f"{label} {event.payload['number']} posted",
            body=f"{event.payload.get('party_name', '')} — total {event.payload['total']}".strip(),
            link="/invoicing",
            payload={"invoice_id": event.payload["invoice_id"]},
        )
        await session.commit()


async def _on_payment_recorded(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        recipients = await _recipients_with(session, "invoicing.payment.read")
        direction = "in" if event.payload["direction"] == "in" else "out"
        await _notify_many(
            session,
            recipients,
            type_="payment_recorded",
            title=f"Payment {event.payload['number']} recorded ({event.payload['amount']})",
            body="Money in" if direction == "in" else "Money out",
            link="/invoicing/payments",
            payload={"payment_id": event.payload["payment_id"]},
        )
        await session.commit()


async def notify_overdue_invoices(session, org_id: uuid.UUID) -> int:  # noqa: ANN001
    """Lazy overdue detection — called when clients poll the unread count.
    One notification per overdue invoice, deduped forever per invoice."""
    from app.modules.invoicing.models import Invoice

    today = date.today()
    overdue = (
        await session.scalars(
            select(Invoice).where(
                Invoice.org_id == org_id,
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
        balance = Decimal(str(invoice.total)) - Decimal(str(invoice.amount_paid))
        created += await _notify_many(
            session,
            recipients,
            type_="invoice_overdue",
            title=f"Invoice {invoice.number} is overdue",
            body=f"Open balance {balance} since {invoice.due_date}",
            link="/invoicing",
            payload={"invoice_id": str(invoice.id)},
            dedupe_key=f"overdue:{invoice.id}",
        )
    return created


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
