"""Guarded staging-only proof of an actual D1 commit between report pages."""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from erp_cf.bootstrap import staging_secret
from erp_cf.db import D1Database, plain
from erp_cf.errors import ConflictError, DomainError, PermissionDeniedError, ValidationError
from erp_cf.identity import bearer, current_actor, environment
from erp_cf.reporting import Reports
from erp_cf.store import Repository, wire

router = APIRouter()


class InterleavingInput(BaseModel):
    invoice_id: uuid.UUID


class InterleavingDatabase:
    def __init__(self, database, target_cursor, commit):
        self.database, self.target_cursor, self.commit = database, target_cursor, commit
        self.changed, self.pages = False, 0

    async def first(self, sql, params=()):
        return await self.database.first(sql, params)

    async def all(self, sql, params=()):
        rows = await self.database.all(sql, params)
        if "FROM invoicing_invoice_lines l" in sql and "AS _cursor0" in sql:
            self.pages += 1
            if not self.changed and any(row["_cursor0"] == self.target_cursor for row in rows):
                self.changed = True
                # This is the real private command binding, not a SQL/test double.
                await self.commit()
        return rows


@router.post("/internal/proofs/reports/void-after-first-page")
async def void_after_first_page(body: InterleavingInput, request: Request):
    env = environment(request)
    staging_secret(env, request.headers.get("x-bootstrap-token", ""))
    actor = await current_actor(request)
    if not actor.is_superuser:
        raise PermissionDeniedError("Only the staging administrator may run this proof")
    actor.require("reports.view")
    actor.require("invoicing.invoice.void")
    key = request.headers.get("idempotency-key") or uuid.uuid4().hex
    if not 1 <= len(key) <= 128:
        raise ValidationError("Idempotency key must contain 1 to 128 characters")
    db, org_id = D1Database(env.DB), str(env.ORG_ID)
    await Repository(db, "invoicing_invoices", org_id).get(str(body.invoice_id))
    lines = await db.first(
        "SELECT CAST(MIN(rowid) AS TEXT) AS first_line,CAST(COUNT(*) AS TEXT) AS n "
        "FROM invoicing_invoice_lines WHERE invoice_id=?",
        (str(body.invoice_id),),
    )
    if int(lines["n"]) <= Reports.page_size:
        raise ConflictError("The proof requires more than one page of invoice lines")

    async def commit():
        result = plain(
            await env.COMMANDS.getByName(org_id).execute(
                {
                    "operation": "invoicing.invoice.void",
                    "token": bearer(request),
                    "key": key,
                    "payload": {"id": str(body.invoice_id)},
                }
            )
        )
        if result["status"] != 200:
            error = result["body"]["error"]
            raise DomainError(error["detail"], code=error["code"], status=result["status"])

    intercepted = InterleavingDatabase(db, lines["first_line"], commit)
    reader = Reports(intercepted, org_id)
    before = await reader.epoch()
    result = await reader.read("tax_summary")
    return {
        "changed": intercepted.changed,
        "page_queries": intercepted.pages,
        "before_epoch": before,
        "after_epoch": await reader.epoch(),
        "rows": wire(result),
    }
