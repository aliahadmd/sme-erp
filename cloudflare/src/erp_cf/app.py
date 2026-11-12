"""ERP application factory shared by Workers and native contract tests."""

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from erp_cf.auth import router as auth_router
from erp_cf.bootstrap import router as bootstrap_router
from erp_cf.db import D1Database
from erp_cf.errors import register_handlers
from erp_cf.identity import environment


def create_app(test_environment=None):
    app = FastAPI(title="SME ERP", version="0.1.0", docs_url="/api/docs")
    register_handlers(app)
    app.include_router(auth_router)
    app.include_router(bootstrap_router)

    @app.middleware("http")
    async def context(request: Request, call_next):
        if test_environment is not None:
            request.scope["env"] = test_environment
        request_id = request.headers.get("x-request-id", "")[:128] or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/healthz")
    @app.get("/readyz")
    async def health(request: Request):
        try:
            result = await D1Database(environment(request).DB).first("SELECT 1 AS ready")
            good = result["ready"] == 1
        except Exception:
            good = False
        return JSONResponse(
            {"status": "ok" if good else "down", "checks": {"d1": "ok" if good else "down"}},
            status_code=200 if good else 503,
        )

    @app.get("/api/meta")
    async def meta(request: Request):
        return {
            "name": "SME ERP",
            "version": "0.1.0",
            "environment": str(environment(request).ENVIRONMENT),
        }

    return app
