"""Restricted operational status, quarantine replay and staging scheduler proof."""

import re
import time
import uuid
from typing import Literal

from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from erp_cf.background import tick
from erp_cf.bootstrap import staging_secret
from erp_cf.command_bus import invoke
from erp_cf.db import D1Database, Statement, plain
from erp_cf.errors import NotFoundError, PermissionDeniedError, ValidationError
from erp_cf.identity import current_actor, environment, require
from erp_cf.maintenance import state as maintenance_state

router = APIRouter()


@router.get("/api/jobs/status", dependencies=[Depends(require("core.settings.read"))])
async def status(request: Request):
    env = environment(request)
    db, org_id = D1Database(env.DB), str(env.ORG_ID)
    results = await db.batch(
        [
            Statement(
                "SELECT delivery_state AS state,CAST(COUNT(*) AS TEXT) AS n "
                "FROM cf_outbox WHERE json_extract(payload_json,'$.org_id')=? "
                "GROUP BY delivery_state",
                (org_id,),
            ),
            Statement(
                "SELECT status AS state,CAST(COUNT(*) AS TEXT) AS n "
                "FROM cf_mail_outbox WHERE org_id=? GROUP BY status",
                (org_id,),
            ),
            Statement(
                "SELECT state,CAST(COUNT(*) AS TEXT) AS n "
                "FROM cf_dead_letters WHERE org_id=? GROUP BY state",
                (org_id,),
            ),
            Statement(
                "SELECT last_trigger_at,last_success_at,last_error "
                "FROM cf_scheduler_state WHERE org_id=?",
                (org_id,),
            ),
            Statement(
                "SELECT kind,run_key,on_date,status,cursor,CAST(version AS TEXT) AS version,"
                "CAST(processed AS TEXT) AS processed,started_at,updated_at,finished_at "
                "FROM cf_job_runs WHERE org_id=? ORDER BY updated_at DESC LIMIT 12",
                (org_id,),
            ),
        ]
    )

    def counts(index):
        return {plain(row)["state"]: int(plain(row)["n"]) for row in results[index]["results"]}

    scheduler = results[3]["results"]
    ai = {"enabled": str(getattr(env, "AI_ENABLED", "false")) == "true"}
    if ai["enabled"]:
        try:
            ai["budget"] = plain(await env.AI_BUDGETS.getByName(org_id).status())
        except Exception:
            ai["budget"] = {"status": "unavailable"}
    return {
        "enabled": str(getattr(env, "JOBS_ENABLED", "false")) == "true",
        "email_mode": str(getattr(env, "EMAIL_MODE", "disabled")),
        "maintenance": await maintenance_state(db),
        "outbox": counts(0),
        "mail": counts(1),
        "ai": ai,
        "dead_letters": counts(2),
        "scheduler": plain(scheduler[0]) if scheduler else None,
        "runs": [plain(row) for row in results[4]["results"]],
    }


@router.get("/api/jobs/dead-letters", dependencies=[Depends(require("core.settings.read"))])
async def dead_letters(request: Request, params: PageParamsDep):
    env = environment(request)
    db, org_id = D1Database(env.DB), str(env.ORG_ID)
    results = await db.batch(
        [
            Statement(
                "SELECT id,event_id,provider_message_id,reason,state,created_at,"
                "last_seen_at,replayed_at,CAST(delivery_generation AS TEXT) AS generation "
                "FROM cf_dead_letters WHERE org_id=? ORDER BY created_at DESC,id DESC "
                "LIMIT ? OFFSET ?",
                (org_id, params.limit, params.offset),
            ),
            Statement(
                "SELECT CAST(COUNT(*) AS TEXT) AS n FROM cf_dead_letters WHERE org_id=?", (org_id,)
            ),
        ]
    )
    return {
        "items": [plain(row) for row in results[0]["results"]],
        "total": int(plain(results[1]["results"][0])["n"]),
        "limit": params.limit,
        "offset": params.offset,
    }


@router.post("/api/jobs/dead-letters/{identity}/replay")
async def replay(identity: str, request: Request):
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValidationError("Invalid dead letter ID")
    return await invoke(request, "jobs.dead_letter.replay", {"id": identity})


def mail_id(identity):
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValidationError("Invalid mail ID")


@router.get("/api/jobs/mail", dependencies=[Depends(require("core.settings.read"))])
async def mail_status(request: Request, params: PageParamsDep):
    env = environment(request)
    result = await D1Database(env.DB).batch(
        [
            Statement(
                "SELECT id,status,kind,source_id,attempt_id,provider_id,last_error,claim_until,"
                "created_at,completed_at,CAST(version AS TEXT) AS version "
                "FROM cf_mail_outbox WHERE org_id=? ORDER BY created_at DESC,id DESC "
                "LIMIT ? OFFSET ?",
                (str(env.ORG_ID), params.limit, params.offset),
            ),
            Statement(
                "SELECT CAST(COUNT(*) AS TEXT) AS n FROM cf_mail_outbox WHERE org_id=?",
                (str(env.ORG_ID),),
            ),
        ]
    )
    return {
        "items": [plain(row) for row in result[0]["results"]],
        "total": int(plain(result[1]["results"][0])["n"]),
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get(
    "/api/jobs/mail/{identity}/attempts", dependencies=[Depends(require("core.settings.read"))]
)
async def mail_attempts(identity: str, request: Request, params: PageParamsDep):
    mail_id(identity)
    env = environment(request)
    db, org = D1Database(env.DB), str(env.ORG_ID)
    if not await db.first("SELECT id FROM cf_mail_outbox WHERE id=? AND org_id=?", (identity, org)):
        raise NotFoundError("Mail intent not found")
    result = await db.batch(
        [
            Statement(
                "SELECT id,event_id,CAST(generation AS TEXT) AS generation,state,payload_hash,"
                "provider_id,error_code,created_at,finished_at FROM cf_mail_attempts "
                "WHERE org_id=? AND mail_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (org, identity, params.limit, params.offset),
            ),
            Statement(
                "SELECT CAST(COUNT(*) AS TEXT) AS n FROM cf_mail_attempts "
                "WHERE org_id=? AND mail_id=?",
                (org, identity),
            ),
        ]
    )
    return {
        "items": [plain(row) for row in result[0]["results"]],
        "total": int(plain(result[1]["results"][0])["n"]),
        "limit": params.limit,
        "offset": params.offset,
    }


class MailResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["retry", "mark_sent", "cancel"]
    reason: str = Field(min_length=5, max_length=500)
    allow_possible_duplicate: bool = Field(default=False, strict=True)
    provider_id: str | None = Field(default=None, min_length=1, max_length=256)


@router.post("/api/jobs/mail/{identity}/resolve")
async def mail_resolve(identity: str, body: MailResolutionInput, request: Request):
    mail_id(identity)
    return await invoke(request, "mail.resolve", {"id": identity, "body": body.model_dump()})


class TickInput(BaseModel):
    force_maintenance: bool = False


@router.post("/internal/jobs/tick")
async def staging_tick(body: TickInput, request: Request):
    env = environment(request)
    staging_secret(env, request.headers.get("x-bootstrap-token", ""))
    actor = await current_actor(request)
    if not actor.is_superuser:
        raise PermissionDeniedError("Only the staging administrator may run this proof")
    key = request.headers.get("idempotency-key") or uuid.uuid4().hex
    if not 1 <= len(key) <= 128:
        raise ValidationError("Invalid scheduler key")
    return await tick(
        env,
        int(time.time() * 1000),
        force=body.force_maintenance,
        run_key="proof:" + key if body.force_maintenance else None,
    )
