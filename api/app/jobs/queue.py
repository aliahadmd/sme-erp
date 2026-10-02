"""Job queue — thin enqueue helper with an inline fallback.

JOBS_MODE=inline (default): jobs execute synchronously in the caller's
process — tests and dev-without-worker keep working.
JOBS_MODE=redis: jobs are enqueued to arq and executed by the worker service.

Every job function registers itself with @register_job so both modes and the
arq WorkerSettings share one list of job names.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_registry: dict[str, Callable[..., Awaitable[Any]]] = {}
_arq_pool = None


def register_job(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    _registry[fn.__name__] = fn
    return fn


def job_names() -> list[str]:
    return sorted(_registry)


async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings

        _arq_pool = await create_pool(
            RedisSettings.from_dsn(get_settings().redis_url),
        )
    return _arq_pool


async def enqueue(name: str, **kwargs: Any) -> None:
    if name not in _registry:
        raise KeyError(f"Unknown job: {name}")
    settings = get_settings()
    if settings.jobs_mode == "redis":
        pool = await _get_arq_pool()
        await pool.enqueue_job(name, **kwargs)
        logger.info("job_enqueued", job=name)
        return
    fn = _registry[name]
    logger.info("job_inline_start", job=name)
    await fn({}, **kwargs)
    logger.info("job_inline_done", job=name)
