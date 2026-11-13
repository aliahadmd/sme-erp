"""Restricted backup metadata and isolated recovery requests; artifacts stay private."""

import json
import uuid

from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Request

from erp_cf.backup_format import attribute
from erp_cf.backup_rpc import call as rpc
from erp_cf.backup_state import load
from erp_cf.command_bus import invoke
from erp_cf.db import D1Database, Statement, plain
from erp_cf.errors import DomainError, NotFoundError, PermissionDeniedError, ValidationError
from erp_cf.identity import bearer, current_actor, environment, require

router = APIRouter()


def enabled(env):
    if str(getattr(env, "BACKUPS_ENABLED", "false")) != "true":
        raise DomainError("Backups are disabled", code="backups_disabled", status=503)


def valid_id(identity):
    try:
        uuid.UUID(identity)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid backup/recovery ID") from exc


def public(row):
    fields = {
        "id",
        "backup_id",
        "target_database_id",
        "status",
        "phase",
        "table_index",
        "page_index",
        "rows",
        "bytes",
        "created_at",
        "updated_at",
        "completed_at",
        "last_error",
        "manifest_hash",
        "version",
    }
    result = {key: value for key, value in row.items() if key in fields}
    if row.get("result_json"):
        result["result"] = json.loads(row["result_json"])
    return result


@router.get("/api/backup-archive", dependencies=[Depends(require("core.settings.read"))])
async def archive(request: Request, cursor: str | None = None):
    env = environment(request)
    enabled(env)
    if cursor is not None and len(cursor) > 4096:
        raise ValidationError("Invalid archive cursor")
    options = {"prefix": f"commits/{env.ORG_ID}/", "limit": 100}
    if cursor:
        options["cursor"] = cursor
    result = await env.BACKUPS.list(options)
    return {
        "items": [
            {"id": attribute(obj, "key").rsplit("/", 1)[1].removesuffix(".json")}
            for obj in attribute(result, "objects")
        ],
        "cursor": attribute(result, "cursor") if attribute(result, "truncated") else None,
    }


@router.post("/api/backup-archive/{identity}/import")
async def import_archive(identity: str, request: Request):
    valid_id(identity)
    env = environment(request)
    enabled(env)
    key = request.headers.get("idempotency-key") or uuid.uuid4().hex
    if not 1 <= len(key) <= 128:
        raise ValidationError("Invalid idempotency key")
    return await rpc(
        env.BACKUP_CONTROL.getByName(str(env.ORG_ID)),
        "import_backup",
        bearer(request),
        identity,
        key,
    )


@router.get("/api/backups", dependencies=[Depends(require("core.settings.read"))])
async def listing(request: Request, params: PageParamsDep):
    db, org = D1Database(environment(request).DB), str(environment(request).ORG_ID)
    result = await db.batch(
        [
            Statement(
                "SELECT id FROM platform_backup_runs WHERE org_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (org, params.limit, params.offset),
            ),
            Statement(
                "SELECT CAST(COUNT(*) AS TEXT) AS n FROM platform_backup_runs WHERE org_id=?",
                (org,),
            ),
        ]
    )
    return {
        "items": [
            public(await load(db, "platform_backup_runs", plain(row)["id"]))
            for row in result[0]["results"]
        ],
        "total": int(plain(result[1]["results"][0])["n"]),
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get("/api/backups/{identity}", dependencies=[Depends(require("core.settings.read"))])
async def backup_status(identity: str, request: Request):
    valid_id(identity)
    env = environment(request)
    row = await load(D1Database(env.DB), "platform_backup_runs", identity)
    if row["org_id"] != str(env.ORG_ID):
        raise NotFoundError()
    return public(row)


@router.post("/api/backups", status_code=202)
async def request_backup(request: Request):
    enabled(environment(request))
    return await invoke(request, "backups.request", {})


@router.post("/api/backups/{identity}/restore", status_code=202)
async def request_restore(identity: str, request: Request):
    valid_id(identity)
    env = environment(request)
    enabled(env)
    if str(getattr(env, "RESTORE_ENABLED", "false")) != "true":
        raise PermissionDeniedError("Isolated recovery is disabled")
    target = str(getattr(env, "RESTORE_DATABASE_ID", ""))
    valid_id(target)
    return await invoke(
        request, "backups.restore", {"backup_id": identity, "target_database_id": target}
    )


@router.post("/api/backups/{identity}/cancel")
async def cancel(identity: str, request: Request):
    valid_id(identity)
    env = environment(request)
    enabled(env)
    return await rpc(
        env.BACKUP_CONTROL.getByName(str(env.ORG_ID)), "cancel", bearer(request), identity
    )


@router.get("/api/recoveries/{identity}", dependencies=[Depends(require("core.settings.read"))])
async def restore_status(identity: str, request: Request):
    valid_id(identity)
    env = environment(request)
    row = await load(D1Database(env.DB), "platform_restore_runs", identity)
    if row["org_id"] != str(env.ORG_ID):
        raise NotFoundError()
    return public(row)


@router.post("/api/backups/advance")
async def advance(request: Request):
    env, actor = environment(request), await current_actor(request)
    actor.require("core.settings.update")
    if not actor.is_superuser:
        raise PermissionDeniedError("Only administrators may advance recovery operations")
    enabled(env)
    return await rpc(env.BACKUP_CONTROL.getByName(str(env.ORG_ID)), "advance")
