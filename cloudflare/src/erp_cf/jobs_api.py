"""Restricted operational status, quarantine replay and staging scheduler proof."""

import re
import time
import uuid

from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from erp_cf.background import tick
from erp_cf.bootstrap import staging_secret
from erp_cf.command_bus import invoke
from erp_cf.db import D1Database, Statement, plain
from erp_cf.errors import PermissionDeniedError, ValidationError
from erp_cf.identity import current_actor, environment, require

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
    return {
        "enabled": str(getattr(env, "JOBS_ENABLED", "false")) == "true",
        "email_mode": str(getattr(env, "EMAIL_MODE", "disabled")),
        "outbox": counts(0),
        "mail": counts(1),
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
