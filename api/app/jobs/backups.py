"""Database backup job — pg_dump (custom format) into BACKUP_DIR, uploaded to
S3 under BACKUP_S3_PREFIX, with grandfather-father-son retention pruning
applied to both copies.

pg_dump ships in the PRODUCTION api image (PGDG client). In dev (no pg_dump
in the slim image) the job skips itself — use `make backup` instead, which
runs pg_dump inside the postgres container.

Restore: `pg_restore --clean --if-exists --dbname <url> erp-<stamp>.dump`
(download from s3://<bucket>/<prefix> first if the local copy is gone).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.queue import register_job

logger = get_logger(__name__)


def _stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(Path(name).stem.replace("erp-", ""), "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _prune_names(
    names: list[str], keep_daily: int, keep_weekly: int, keep_monthly: int
) -> list[str]:
    """Grandfather-father-son pruning on timestamped dump names; returns the
    names to DELETE."""
    dated = sorted((n for n in names if _stamp(n) is not None), key=lambda n: _stamp(n))
    if len(dated) <= keep_daily:
        return []
    keep: set[str] = set(dated[-keep_daily:])
    weekly: set[str] = set()
    monthly: set[str] = set()
    for name in reversed(dated):
        dt = _stamp(name)
        assert dt is not None
        if len(weekly) < keep_weekly and dt.isocalendar().weekday == 1:  # Mondays
            weekly.add(name)
        if len(monthly) < keep_monthly and dt.day <= 7:
            monthly.add(name)
    keep |= weekly | monthly
    return [n for n in dated if n not in keep]


def _prune(directory: Path, keep_daily: int, keep_weekly: int, keep_monthly: int) -> list[Path]:
    """Local-file variant of `_prune_names`."""
    files = {f.name: f for f in directory.glob("erp-*.dump")}
    return [files[n] for n in _prune_names(list(files), keep_daily, keep_weekly, keep_monthly)]


def _libpq_url_without_password(database_url: str) -> tuple[str, str | None]:
    """SQLAlchemy URL → (libpq URL without password, password). The password
    travels via PGPASSWORD so it never appears on the process argv."""
    parts = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    password = parts.password
    netloc = parts.hostname or ""
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)), password


def _dump(target: Path) -> None:
    settings = get_settings()
    url, password = _libpq_url_without_password(settings.database_url)
    env = {**os.environ}
    if password:
        env["PGPASSWORD"] = password
    old_umask = os.umask(0o077)  # dump files are owner-only (0600)
    try:
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(target), "--dbname", url],
            capture_output=True,
            text=True,
            env=env,
            timeout=settings.backup_timeout_seconds,
        )
    finally:
        os.umask(old_umask)
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        logger.error("backup_failed", stderr=result.stderr[-2000:])
        raise RuntimeError("pg_dump failed")


def _s3_client():  # noqa: ANN202
    import boto3
    from botocore.config import Config

    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def _upload_and_prune_s3(target: Path) -> int:
    """Upload the dump and prune old S3 copies with the same GFS policy."""
    settings = get_settings()
    prefix = settings.backup_s3_prefix
    client = _s3_client()
    client.upload_file(str(target), settings.s3_bucket, f"{prefix}{target.name}")
    listed = client.list_objects_v2(Bucket=settings.s3_bucket, Prefix=prefix)
    names = [obj["Key"][len(prefix) :] for obj in listed.get("Contents", [])]
    doomed = _prune_names(
        names, settings.backup_keep_daily, settings.backup_keep_weekly, settings.backup_keep_monthly
    )
    for name in doomed:
        client.delete_object(Bucket=settings.s3_bucket, Key=f"{prefix}{name}")
    return len(doomed)


@register_job
async def backup_database(ctx: dict[str, Any]) -> dict[str, str]:
    if shutil.which("pg_dump") is None:
        logger.info("backup_skipped", reason="pg_dump not installed (use make backup in dev)")
        return {"status": "skipped"}

    settings = get_settings()
    dumps_dir = Path(settings.backup_dir)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    target = dumps_dir / f"erp-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.dump"

    # pg_dump and boto3 are blocking — keep them off the worker's event loop.
    await asyncio.to_thread(_dump, target)
    size = target.stat().st_size

    uploaded = "skipped"
    if settings.backup_s3_prefix:
        try:
            pruned_remote = await asyncio.to_thread(_upload_and_prune_s3, target)
            uploaded = f"s3://{settings.s3_bucket}/{settings.backup_s3_prefix}{target.name}"
            logger.info("backup_uploaded", key=uploaded, pruned_remote=pruned_remote)
        except Exception:
            # The local copy still exists; surface loudly but keep the dump.
            logger.exception("backup_upload_failed", file=target.name)
            uploaded = "failed"

    pruned = _prune(
        dumps_dir,
        settings.backup_keep_daily,
        settings.backup_keep_weekly,
        settings.backup_keep_monthly,
    )
    for path in pruned:
        path.unlink(missing_ok=True)
    logger.info("backup_done", file=target.name, size=size, pruned=len(pruned), s3=uploaded)
    if uploaded == "failed":
        raise RuntimeError("backup written locally but the S3 upload failed")
    return {"file": target.name, "size": str(size), "s3": uploaded}


@register_job
async def prune_audit_logs(ctx: dict[str, Any]) -> dict[str, int]:
    """Delete audit rows older than AUDIT_RETENTION_DAYS (default 365)."""
    from sqlalchemy import delete, select

    from app.core.db import SessionFactory
    from app.modules.core.models import AuditLog, Organization

    retention_days = get_settings().audit_retention_days
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
