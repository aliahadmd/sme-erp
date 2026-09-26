"""Background jobs — registered via @register_job, executed by the arq worker
(JOBS_MODE=redis) or inline (JOBS_MODE=inline)."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.jobs.queue import register_job

logger = get_logger(__name__)


@register_job
async def check_overdue_invoices(ctx: dict[str, Any]) -> dict[str, int]:
    """Scan every organization for overdue invoices and create the deduped
    notifications (same logic as the phase-1 lazy path)."""
    from sqlalchemy import select

    from app.core.db import SessionFactory
    from app.modules.core.models import Organization
    from app.modules.core.notification_events import notify_overdue_invoices

    created = 0
    async with SessionFactory() as session:
        orgs = (await session.scalars(select(Organization.id))).all()
        for org_id in orgs:
            created += await notify_overdue_invoices(session, org_id)
        await session.commit()
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
            if not await redis.ttl(key):
                await redis.delete(key)
                removed += 1
    finally:
        await redis.aclose()
    logger.info("login_counters_purged", removed=removed)
    return {"removed": removed}
