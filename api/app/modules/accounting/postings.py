"""Event subscribers — the bridge from business events to the books.

Each handler opens its own session (events fire after the publishing
transaction committed) and posts a balanced journal entry. Registered once
at application startup via `register()`.
"""

import uuid
from datetime import date
from decimal import Decimal

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.modules.accounting.service import (
    post_entry,
    resolve_account,
    resolve_payment_account,
)
from app.shared.events import Event, subscribe

logger = get_logger(__name__)


def _d(value) -> Decimal:  # noqa: ANN001
    return Decimal(str(value or "0"))


async def _on_invoice_posted(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        is_ar = event.payload["invoice_type"] == "ar"
        total = _d(event.payload["total"])
        net = _d(event.payload["subtotal"]) - _d(event.payload["discount_total"])
        tax = _d(event.payload["tax_total"])
        lines = []
        if is_ar:
            lines.append((await resolve_account(session, org_id, "ar"), total, Decimal("0")))
            lines.append(
                (await resolve_account(session, org_id, "sales_revenue"), Decimal("0"), net)
            )
            if tax:
                lines.append(
                    (await resolve_account(session, org_id, "tax_payable"), Decimal("0"), tax)
                )
        else:
            # Phase-1 simplification: AP booked gross (input-tax recovery deferred).
            lines.append((await resolve_account(session, org_id, "purchases"), total, Decimal("0")))
            lines.append((await resolve_account(session, org_id, "ap"), Decimal("0"), total))
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"{'AR' if is_ar else 'AP'} invoice {event.payload['number']}",
            source_type="ar_invoice" if is_ar else "ap_invoice",
            source_id=uuid.UUID(event.payload["invoice_id"]),
            lines=lines,
        )
        await session.commit()


async def _on_invoice_voided(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        is_ar = event.payload["invoice_type"] == "ar"
        total = _d(event.payload["total"])
        net = _d(event.payload["subtotal"]) - _d(event.payload["discount_total"])
        tax = _d(event.payload["tax_total"])
        lines = []
        if is_ar:
            lines.append((await resolve_account(session, org_id, "ar"), Decimal("0"), total))
            lines.append(
                (await resolve_account(session, org_id, "sales_revenue"), net, Decimal("0"))
            )
            if tax:
                lines.append(
                    (await resolve_account(session, org_id, "tax_payable"), tax, Decimal("0"))
                )
        else:
            lines.append((await resolve_account(session, org_id, "purchases"), Decimal("0"), total))
            lines.append((await resolve_account(session, org_id, "ap"), total, Decimal("0")))
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"Reversal: invoice {event.payload['number']}",
            source_type="reversal",
            source_id=uuid.UUID(event.payload["invoice_id"]),
            lines=lines,
        )
        await session.commit()


async def _on_payment_recorded(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        amount = _d(event.payload["amount"])
        money_account = await resolve_payment_account(session, org_id, event.payload["method"])
        incoming = event.payload["direction"] == "in"
        lines = []
        if incoming:
            lines.append((money_account, amount, Decimal("0")))
            lines.append((await resolve_account(session, org_id, "ar"), Decimal("0"), amount))
        else:
            lines.append((await resolve_account(session, org_id, "ap"), amount, Decimal("0")))
            lines.append((money_account, Decimal("0"), amount))
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"Payment {event.payload['number']}",
            source_type="payment",
            source_id=uuid.UUID(event.payload["payment_id"]),
            lines=lines,
        )
        await session.commit()


async def _on_payment_voided(event: Event) -> None:
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        amount = _d(event.payload["amount"])
        money_account = await resolve_payment_account(session, org_id, event.payload["method"])
        incoming = event.payload["direction"] == "in"
        lines = []
        if incoming:
            lines.append((money_account, Decimal("0"), amount))
            lines.append((await resolve_account(session, org_id, "ar"), amount, Decimal("0")))
        else:
            lines.append((await resolve_account(session, org_id, "ap"), Decimal("0"), amount))
            lines.append((money_account, amount, Decimal("0")))
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"Reversal: payment {event.payload['number']}",
            source_type="reversal",
            source_id=uuid.UUID(event.payload["payment_id"]),
            lines=lines,
        )
        await session.commit()


async def _on_delivery_posted(event: Event) -> None:
    total_cogs = sum((_d(m.get("cogs")) for m in event.payload.get("moves", [])), Decimal("0"))
    if total_cogs == 0:
        return
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"COGS for delivery {event.payload['number']}",
            source_type="delivery",
            source_id=uuid.UUID(event.payload["delivery_id"]),
            lines=[
                (await resolve_account(session, org_id, "cogs"), total_cogs, Decimal("0")),
                (await resolve_account(session, org_id, "inventory"), Decimal("0"), total_cogs),
            ],
        )
        await session.commit()


async def _on_adjustment_posted(event: Event) -> None:
    value = _d(event.payload.get("value"))
    if value == 0:
        return
    qty = _d(event.payload.get("qty"))
    async with SessionFactory() as session:
        org_id = event.org_id
        assert org_id is not None
        if qty < 0:
            lines = [
                (await resolve_account(session, org_id, "stock_correction"), -value, Decimal("0")),
                (await resolve_account(session, org_id, "inventory"), Decimal("0"), -value),
            ]
        else:
            lines = [
                (await resolve_account(session, org_id, "inventory"), value, Decimal("0")),
                (await resolve_account(session, org_id, "stock_correction"), Decimal("0"), value),
            ]
        await post_entry(
            session,
            org_id,
            entry_date=date.today(),
            memo=f"Stock adjustment {event.payload['number']}",
            source_type="adjustment",
            source_id=uuid.UUID(event.payload["adjustment_id"]),
            lines=lines,
        )
        await session.commit()


def register() -> None:
    subscribe("invoice.posted", _on_invoice_posted)
    subscribe("invoice.voided", _on_invoice_voided)
    subscribe("payment.recorded", _on_payment_recorded)
    subscribe("payment.voided", _on_payment_voided)
    subscribe("delivery.posted", _on_delivery_posted)
    subscribe("adjustment.posted", _on_adjustment_posted)
    logger.info("accounting_subscribers_registered")
