"""Private organization command dispatch with one atomic D1 write boundary."""

import asyncio
import logging
import uuid
from decimal import localcontext

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError as PydanticValidationError

from erp_cf.db import D1Database
from erp_cf.errors import DomainError, NotFoundError, ValidationError
from erp_cf.identity import bearer, from_token
from erp_cf.store import UnitOfWork, wire


class Coordinator:
    def __init__(self, env):
        self.env = env
        self.lock = asyncio.Lock()

    async def execute(self, envelope):
        from erp_cf.accounting_commands import COMMANDS as ACCOUNTING
        from erp_cf.core_commands import COMMANDS as CORE
        from erp_cf.documents import COMMANDS as DOCUMENTS
        from erp_cf.inventory_commands import COMMANDS as INVENTORY
        from erp_cf.invoicing_commands import COMMANDS as INVOICING
        from erp_cf.master_commands import COMMANDS as MASTER

        operation = envelope.get("operation", "")
        try:
            spec = (CORE | MASTER | ACCOUNTING | DOCUMENTS | INVENTORY | INVOICING).get(operation)
            if spec is None:
                raise NotFoundError("Unknown command")
            async with self.lock:
                actor = await from_token(self.env, envelope["token"])
                permission, status, handler = spec
                if permission:
                    actor.require(permission)
                fingerprint_key = str(getattr(self.env, "COMMAND_HASH_KEY", ""))
                if len(fingerprint_key) < 32:
                    raise RuntimeError("COMMAND_HASH_KEY must contain at least 32 characters")
                plan = UnitOfWork(
                    D1Database(self.env.DB),
                    str(self.env.ORG_ID),
                    actor.id,
                    envelope["key"],
                    operation,
                    envelope["payload"],
                    fingerprint_key=fingerprint_key,
                )
                existing = await plan.replay()
                if existing is not None:
                    return existing
                actor.guard(plan)
                with localcontext() as context:
                    context.prec = 80
                    response = await handler(plan, actor, envelope["payload"])
                    return await plan.commit({"status": status, "body": wire(response)})
        except DomainError as exc:
            return {"status": exc.status, "body": exc.response()}
        except PydanticValidationError:
            exc = ValidationError("Command validation failed")
            return {"status": exc.status, "body": exc.response()}
        except Exception:
            logging.getLogger("erp").exception("command_failed operation=%s", operation)
            return {
                "status": 500,
                "body": {"error": {"code": "internal_error", "detail": "Internal server error"}},
            }


async def invoke(request: Request, operation: str, payload: dict):
    key = request.headers.get("idempotency-key") or uuid.uuid4().hex
    if not 1 <= len(key) <= 128:
        raise ValidationError("Idempotency key must contain 1 to 128 characters")
    env = request.scope["env"]
    result = await env.COMMANDS.getByName(str(env.ORG_ID)).execute(
        {
            "operation": operation,
            "token": bearer(request),
            "key": key,
            "payload": wire(payload),
        }
    )
    if result["status"] == 204:
        return Response(status_code=204)
    return JSONResponse(result["body"], status_code=result["status"])
