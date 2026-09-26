"""Background job infrastructure.

- `queue.enqueue(name, **kwargs)` — call sites; inline or redis-backed.
- `maintenance` — job implementations (self-registering).
- `WorkerSettings` — the arq worker entrypoint (`arq app.jobs.WorkerSettings`).
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.jobs import maintenance
from app.jobs.queue import enqueue, job_names, register_job  # noqa: F401


async def worker_heartbeat(ctx) -> None:  # noqa: ANN001
    """Runs every minute on the worker; healthz reads this key."""
    redis = ctx.get("redis")
    if redis:
        from datetime import UTC, datetime

        await redis.set("jobs:heartbeat", datetime.now(UTC).isoformat(), ex=180)


class WorkerSettings:
    functions = [
        maintenance.check_overdue_invoices,
        maintenance.purge_login_counters,
        worker_heartbeat,
    ]
    cron_jobs = [
        cron(maintenance.check_overdue_invoices, hour=7, minute=0, unique=True),
        cron(maintenance.purge_login_counters, minute={0, 10, 20, 30, 40, 50}, unique=True),
        cron(worker_heartbeat, minute=set(range(60)), unique=True),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 300
    max_tries = 3
