"""Uniform error model: every failure responds as {"error": {"code", "detail", ...}}."""

import json

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


def _json_safe(value):  # noqa: ANN001
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class DomainError(Exception):
    """Base class for expected, user-facing errors."""

    status_code = 400

    def __init__(self, detail: str, *, code: str = "domain_error", status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        if status_code is not None:
            self.status_code = status_code


class AuthenticationError(DomainError):
    status_code = 401

    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(detail, code="authentication_failed")


class PermissionDeniedError(DomainError):
    status_code = 403

    def __init__(self, detail: str = "Permission denied"):
        super().__init__(detail, code="forbidden")


class NotFoundError(DomainError):
    status_code = 404

    def __init__(self, detail: str = "Not found"):
        super().__init__(detail, code="not_found")


class ConflictError(DomainError):
    status_code = 409

    def __init__(self, detail: str = "Conflict"):
        super().__init__(detail, code="conflict")


class ValidationError(DomainError):
    status_code = 422

    def __init__(self, detail: str):
        super().__init__(detail, code="validation_error")


def _error_body(code: str, detail: str, **extra: object) -> dict:
    return {"error": {"code": code, "detail": detail, **extra}}


def register_exception_handlers(app) -> None:  # noqa: ANN001 (FastAPI app)
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.code, exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{k: _json_safe(v) for k, v in err.items()} for err in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Request validation failed", errors=errors),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", path=request.url.path, error=str(exc), exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "Internal server error"),
        )
