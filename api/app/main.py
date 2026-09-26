"""ERP API application factory."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import anyio
import boto3
import structlog
from botocore.config import Config
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis import asyncio as aioredis
from sqlalchemy import text

from app.core.ai import AIClient
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import DomainError, register_exception_handlers
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )
    logger.info("api_started", environment=settings.environment)
    yield
    await app.state.redis.aclose()
    await engine.dispose()
    logger.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.environment)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.redis = None  # created in lifespan
    app.state.s3 = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    from app.modules.accounting import postings as accounting_postings
    from app.modules.accounting.router import router as accounting_router
    from app.modules.catalog.router import router as catalog_router
    from app.modules.core.router import router as core_router
    from app.modules.crm.router import router as crm_router
    from app.modules.inventory.router import router as inventory_router
    from app.modules.invoicing.router import router as invoicing_router
    from app.modules.purchasing.router import router as purchasing_router
    from app.modules.reporting.router import router as reporting_router
    from app.modules.sales.router import router as sales_router

    accounting_postings.register()

    from app.modules.core import notification_events

    notification_events.register()

    app.include_router(core_router, prefix=settings.api_prefix)
    app.include_router(crm_router, prefix=settings.api_prefix)
    app.include_router(catalog_router, prefix=settings.api_prefix)
    app.include_router(sales_router, prefix=settings.api_prefix)
    app.include_router(purchasing_router, prefix=settings.api_prefix)
    app.include_router(inventory_router, prefix=settings.api_prefix)
    app.include_router(invoicing_router, prefix=settings.api_prefix)
    app.include_router(accounting_router, prefix=settings.api_prefix)
    app.include_router(reporting_router, prefix=settings.api_prefix)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex)
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        structlog.contextvars.clear_contextvars()
        return response

    # ---------------------------------------------------------------- health
    async def _check(name: str, coro_factory) -> tuple[str, str]:  # type: ignore[no-untyped-def]
        try:
            await coro_factory()
        except Exception as exc:
            logger.warning("health_check_failed", dependency=name, error=str(exc))
            return name, "down"
        return name, "ok"

    async def _postgres_ok() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def _redis_ok() -> None:
        await app.state.redis.ping()

    async def _s3_ok() -> None:
        bucket = settings.s3_bucket

        def _head() -> None:
            app.state.s3.head_bucket(Bucket=bucket)

        # boto3 is sync — keep it off the event loop.
        await anyio.to_thread.run_sync(_head)

    async def _jobs_status() -> str:
        if settings.jobs_mode != "redis":
            return "inline"
        heartbeat = await app.state.redis.get("jobs:heartbeat")
        return "ok" if heartbeat else "stalled"

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        checks = dict(
            await asyncio.gather(
                _check("postgres", _postgres_ok),
                _check("redis", _redis_ok),
                _check("s3", _s3_ok),
            )
        )
        jobs = await _jobs_status()
        status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        return JSONResponse(
            status_code=200 if status == "ok" else 503,
            content={"status": status, "checks": checks, "jobs": jobs},
        )

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return await healthz()

    # ------------------------------------------------------------------ meta
    class MetaOut(BaseModel):
        name: str
        version: str
        environment: str

    @app.get(f"{settings.api_prefix}/meta", response_model=MetaOut)
    async def meta() -> MetaOut:
        s = get_settings()
        return MetaOut(name=s.app_name, version="0.1.0", environment=s.environment)

    # ------------------------------------------------------------------- ai
    class AIEchoIn(BaseModel):
        prompt: str = Field(min_length=1, max_length=4000)

    from app.modules.core.deps import require_superuser

    @app.post(
        f"{settings.api_prefix}/ai/echo",
        dependencies=[Depends(require_superuser)],
    )
    async def ai_echo(body: AIEchoIn) -> dict[str, Any]:
        client = AIClient(get_settings())
        if not client.enabled:
            raise DomainError(
                "AI is disabled: OPENROUTER_API_KEY is not set",
                code="ai_disabled",
                status_code=503,
            )
        try:
            result = await client.complete(
                system=(
                    "You are a helpful ERP assistant. Echo the user's prompt back "
                    "with a short helpful sentence."
                ),
                prompt=body.prompt,
            )
        except Exception as exc:
            logger.error("ai_call_failed", error=str(exc))
            raise DomainError("AI request failed", code="ai_error", status_code=502) from exc
        return {"text": result.text, "model": result.model}

    return app


app = create_app()
