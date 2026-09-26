"""Database backup job — pg_dump (custom format) into /app/dumps with
grandfather-father-son retention pruning.

pg_dump ships in the PRODUCTION api image (PGDG client). In dev (no pg_dump
in the slim image) the job skips itself — use `make backup` instead, which
runs pg_dump inside the postgres container.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.queue import register_job

logger = get_logger(__name__)

DUMPS_DIR = Path("/app/dumps")


def _prune(directory: Path, keep_daily: int, keep_weekly: int, keep_monthly: int) -> list[Path]:
    """Grandfather-father-son pruning on timestamped dump files."""
    files = sorted(directory.glob("erp-*.dump"))
    if len(files) <= keep_daily:
        return []
    keep: set[Path] = set(files[-keep_daily:])
    weekly: set[Path] = set()
    monthly: set[Path] = set()
    for f in reversed(files):
        stamp = f.stem.replace("erp-", "")
        try:
            dt = datetime.strptime(stamp, "%Y%m%dT%H%M%S")
        except ValueError:
            continue
        if len(weekly) < keep_weekly and dt.isocalendar().weekday == 1:  # Mondays
            weekly.add(f)
        if len(monthly) < keep_monthly and dt.day <= 7:
            monthly.add(f)
    keep |= weekly | monthly
    return [f for f in files if f not in keep]


@register_job
async def backup_database(ctx: dict[str, Any]) -> dict[str, str]:
    if shutil.which("pg_dump") is None:
        logger.info("backup_skipped", reason="pg_dump not installed (use make backup in dev)")
        return {"status": "skipped"}

    settings = get_settings()
    libpq_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = DUMPS_DIR / f"erp-{stamp}.dump"

    result = subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(target), "--dbname", libpq_url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        logger.error("backup_failed", stderr=result.stderr[-2000:])
        raise RuntimeError("pg_dump failed")

    pruned = _prune(DUMPS_DIR, keep_daily=14, keep_weekly=8, keep_monthly=6)
    for p in pruned:
        p.unlink(missing_ok=True)
    logger.info("backup_done", file=target.name, size=target.stat().st_size, pruned=len(pruned))
    return {"file": target.name, "size": str(target.stat().st_size)}


@register_job
async def prune_audit_logs(ctx: dict[str, Any]) -> dict[str, int]:
    """Delete audit rows older than AUDIT_RETENTION_DAYS (default 365)."""
    from datetime import datetime, timedelta

    from sqlalchemy import delete, select

    from app.core.db import SessionFactory
    from app.modules.core.models import AuditLog, Organization

    retention_days = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    deleted = 0
    async with SessionFactory() as session:
        org_ids = (await session.scalars(select(Organization.id))).all()
        for org_id in org_ids:
            result = await session.execute(
                delete(AuditLog).where(
                    AuditLog.org_id == org_id,
                    AuditLog.created_at < cutoff,
                )
            )
            deleted += result.rowcount or 0
        await session.commit()
    logger.info("audit_logs_purged", deleted=deleted, retention_days=retention_days)
    return {"deleted": deleted}
