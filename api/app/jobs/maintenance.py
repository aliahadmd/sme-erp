"""Background jobs — registered via @register_job, executed by the arq worker
(JOBS_MODE=redis) or inline (JOBS_MODE=inline)."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.jobs.queue import register_job

logger = get_logger(__name__)


@register_job
async def check_overdue_invoices(ctx: dict[str, Any]) -> dict[str, int]:
    """Scan every organization for overdue invoices: deduped in-app
    notifications plus ONE reminder email per overdue customer invoice.
    Each organization commits on its own so one failure cannot block others."""
    from sqlalchemy import select

    from app.core.db import SessionFactory
    from app.modules.core.models import Organization
    from app.modules.core.notification_events import (
        flush_overdue_emails,
        notify_overdue_invoices,
    )

    created = 0
    async with SessionFactory() as session:
        orgs = (await session.scalars(select(Organization.id))).all()
    for org_id in orgs:
        try:
            async with SessionFactory() as session:
                created += await notify_overdue_invoices(session, org_id, send_reminders=True)
                await session.commit()
                await flush_overdue_emails(session)
        except Exception:
            logger.exception("overdue_check_failed", org_id=str(org_id))
    logger.info("overdue_check_done", notifications_created=created)
    return {"notifications_created": created}


@register_job
async def purge_login_counters(ctx: dict[str, Any]) -> dict[str, int]:
    """Defensive cleanup of login failure counters that lost their TTL
    (normal counters expire automatically in redis)."""
    from redis.asyncio import from_url

    from app.core.config import get_settings

    redis = from_url(get_settings().redis_url, decode_responses=True)
    removed = 0
    try:
        async for key in redis.scan_iter("login:fail:*", count=200):
            # TTL -1 = key exists without expiry (a normal counter always has one).
            if await redis.ttl(key) == -1:
                await redis.delete(key)
                removed += 1
    finally:
        await redis.aclose()
    logger.info("login_counters_purged", removed=removed)
    return {"removed": removed}


@register_job
async def generate_missing_descriptions(
    ctx: dict[str, Any], org_id: str, limit: int = 25
) -> dict[str, int]:
    """Generate AI description drafts for active products missing descriptions."""
    from sqlalchemy import select

    from app.core.ai import AIClient
    from app.core.config import get_settings
    from app.core.db import SessionFactory
    from app.modules.catalog.models import Product
    from app.modules.core.models import AiDraft

    client = AIClient(get_settings())
    if not client.enabled:
        return {"created": 0, "skipped": "ai_disabled"}

    async with SessionFactory() as session:
        products = (
            await session.scalars(
                select(Product)
                .where(
                    Product.org_id == org_id,
                    Product.status == "active",
                    (Product.description.is_(None)) | (Product.description == ""),
                    # Skip products that already have a draft awaiting review.
                    Product.id.not_in(
                        select(AiDraft.entity_id).where(
                            AiDraft.entity_type == "product", AiDraft.status == "pending"
                        )
                    ),
                )
                .limit(limit)
            )
        ).all()
        created = 0
        for product in products:
            try:
                category = None
                result = await client.complete(
                    system=(
                        "You write concise, factual e-commerce product descriptions "
                        "(2-3 sentences) for an SME ERP. No marketing fluff."
                    ),
                    prompt=(
                        f"Product: {product.name}\nType: {product.type}\n"
                        f"Category: {category or 'uncategorized'}"
                    ),
                )
                session.add(
                    AiDraft(
                        org_id=org_id,
                        entity_type="product",
                        entity_id=product.id,
                        field="description",
                        draft_text=result.text,
                        model=result.model,
                    )
                )
                created += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ai_draft_failed", product=str(product.id), error=str(exc))
        await session.commit()
    logger.info("ai_drafts_created", created=created)
    return {"created": created}


@register_job
async def expire_quotations(ctx: dict[str, Any]) -> dict[str, int]:
    """Mark sent quotations past their valid_until date as expired."""
    from datetime import date

    from sqlalchemy import select

    from app.core.db import SessionFactory
    from app.modules.sales.models import Quotation

    expired = 0
    async with SessionFactory() as session:
        quotes = (
            await session.scalars(
                select(Quotation).where(
                    Quotation.org_id.is_not(None),
                    Quotation.status == "sent",
                    Quotation.valid_until.is_not(None),
                    Quotation.valid_until < date.today(),
                )
            )
        ).all()
        for quote in quotes:
            quote.status = "expired"
            expired += 1
        await session.commit()
    logger.info("quotations_expired", count=expired)
    return {"expired": expired}
