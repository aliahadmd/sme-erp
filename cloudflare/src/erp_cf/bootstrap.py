"""One-time staging bootstrap for the authorized fresh demo database."""

import hmac
import secrets

from app.modules.core.permissions import PERMISSION_CATALOG, SYSTEM_ROLES, system_role_permissions
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field

from erp_cf.crypto import hash_password
from erp_cf.db import D1Database, Statement, advance_business_epoch, assertion
from erp_cf.errors import ConflictError, NotFoundError, PermissionDeniedError
from erp_cf.foundation import records
from erp_cf.identity import environment
from erp_cf.store import tables

router = APIRouter()


class BootstrapInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)
    full_name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(default="Cloudflare ERP Demo", min_length=1, max_length=200)


async def seed(env, body: BootstrapInput):
    db = D1Database(env.DB)
    occupied = await db.first(
        "SELECT (SELECT COUNT(*) FROM core_users) + (SELECT COUNT(*) FROM core_organizations) AS n"
    )
    if occupied["n"]:
        raise ConflictError("The demo is already initialized")
    statements = []

    def add(name, data):
        statement, row = tables()[name].insert(data)
        statements.append(statement)
        return row

    identity = "bootstrap-" + secrets.token_hex(16)
    statements.append(
        assertion(
            identity,
            "NOT EXISTS(SELECT 1 FROM core_users) AND NOT EXISTS(SELECT 1 FROM core_organizations)",
        )
    )
    org = add("core_organizations", {"id": str(env.ORG_ID), "name": body.organization_name})
    branch = add("core_branches", {"org_id": org["id"], "code": "HQ", "name": "Head Office"})
    for name, data in records(org):
        add(name, data)
    permissions = {
        code: add(
            "core_permissions",
            {"code": code, "module": module, "action": action, "description": description},
        )
        for code, module, action, description in PERMISSION_CATALOG
    }
    roles = {}
    for code, definition in SYSTEM_ROLES.items():
        roles[code] = add(
            "core_roles",
            {
                "code": code,
                "name": definition["name"],
                "description": definition["description"],
                "is_system": True,
            },
        )
        for permission in system_role_permissions(code):
            add(
                "core_role_permissions",
                {"role_id": roles[code]["id"], "permission_id": permissions[permission]["id"]},
            )
    user = add(
        "core_users",
        {
            "email": str(body.email).strip().lower(),
            "hashed_password": await hash_password(body.password),
            "full_name": body.full_name,
            "is_superuser": True,
            "default_branch_id": branch["id"],
        },
    )
    add("core_user_roles", {"user_id": user["id"], "role_id": roles["admin"]["id"]})
    add(
        "core_audit_logs",
        {
            "org_id": org["id"],
            "user_id": user["id"],
            "action": "bootstrap",
            "entity_type": "core.organization",
            "entity_id": org["id"],
        },
    )
    statements.append(advance_business_epoch(org["id"]))
    statements.append(Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)))
    try:
        await db.batch(statements)
    except Exception as exc:
        if "constraint failed" in str(exc).lower():
            raise ConflictError(
                "The demo was initialized concurrently or contains conflicting data"
            ) from exc
        raise
    return {
        "organization_id": org["id"],
        "user_id": user["id"],
        "permissions": len(permissions),
        "roles": len(roles),
    }


@router.post("/internal/bootstrap", status_code=201)
async def bootstrap(body: BootstrapInput, request: Request):
    env = environment(request)
    staging_secret(env, request.headers.get("authorization", "").removeprefix("Bearer "))
    return await seed(env, body)


def staging_secret(env, provided):
    if env.ENVIRONMENT != "staging" or getattr(env, "BOOTSTRAP_ENABLED", "false") != "true":
        raise NotFoundError()
    expected = str(getattr(env, "BOOTSTRAP_TOKEN", ""))
    if not expected or not hmac.compare_digest(expected.encode(), provided.encode()):
        raise PermissionDeniedError()


@router.post("/internal/seed-foundation")
async def seed_foundation(request: Request):
    from erp_cf.command_bus import invoke

    staging_secret(environment(request), request.headers.get("x-bootstrap-token", ""))
    return await invoke(request, "core.initialize", {})
