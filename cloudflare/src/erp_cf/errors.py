"""The existing ERP's public error envelope, independent of server libraries."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class DomainError(Exception):
    def __init__(self, detail: str, *, code: str = "domain_error", status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status

    def response(self):
        return {"error": {"code": self.code, "detail": self.detail}}


class AuthenticationError(DomainError):
    def __init__(self, detail="Authentication failed"):
        super().__init__(detail, code="authentication_failed", status=401)


class PermissionDeniedError(DomainError):
    def __init__(self, detail="Permission denied"):
        super().__init__(detail, code="forbidden", status=403)


class NotFoundError(DomainError):
    def __init__(self, detail="Not found"):
        super().__init__(detail, code="not_found", status=404)


class ConflictError(DomainError):
    def __init__(self, detail="Conflict"):
        super().__init__(detail, code="conflict", status=409)


class ValidationError(DomainError):
    def __init__(self, detail):
        super().__init__(detail, code="validation_error", status=422)


def register_handlers(app: FastAPI):
    @app.exception_handler(DomainError)
    async def expected(request: Request, exc: DomainError):
        return JSONResponse(exc.response(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        # Validation may include a submitted password or bearer value in input.
        errors = [
            {k: str(v) if k == "ctx" else v for k, v in error.items() if k not in {"input", "url"}}
            for error in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "detail": "Request validation failed",
                    "errors": errors,
                }
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {"error": {"code": "http_error", "detail": str(exc.detail)}},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logging.getLogger("erp").exception("request_failed path=%s", request.url.path)
        return JSONResponse(
            {"error": {"code": "internal_error", "detail": "Internal server error"}},
            status_code=500,
        )
