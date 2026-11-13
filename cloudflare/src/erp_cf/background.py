"""Native Queues transport, retry quarantine and bounded Cron orchestration."""

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from erp_cf.business_clock import business_time
from erp_cf.db import D1Database, Statement, plain
from erp_cf.store import now

logger = logging.getLogger("erp.jobs")


class EventMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    org_id: uuid.UUID
    event_id: uuid.UUID
    generation: int = Field(ge=0, le=9007199254740991, strict=True)


class JobFailed(Exception):
    def __init__(self, code, *, defer_until=None):
        self.code, self.defer_until = code, defer_until
        super().__init__(code)


def decode_message(body, org_id):
    if not isinstance(body, str) or len(body) > 1024:
        raise JobFailed("invalid_message")
    try:
        message = EventMessage.model_validate_json(body)
    except Exception as exc:
        raise JobFailed("invalid_message") from exc
    if str(message.org_id) != str(org_id):
        raise JobFailed("wrong_organization")
    return message


async def system(env, operation, key, payload):
    response = plain(
        await env.COMMANDS.getByName(str(env.ORG_ID)).run_system(
            {"operation": operation, "key": key, "payload": payload}
        )
    )
    if response["status"] != 200:
        raise JobFailed(response["body"]["error"]["code"])
    return response["body"]


async def dispatch(env, *, limit=100):
    if str(getattr(env, "JOBS_ENABLED", "false")) != "true":
        return {"dispatched": 0}
    db, org_id = D1Database(env.DB), str(env.ORG_ID)
    cutoff = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
    timestamp = now().isoformat()
    rows = await db.all(
        "SELECT id,CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox "
        "WHERE json_extract(payload_json,'$.org_id')=? "
        "AND completed_at IS NULL AND delivery_state IN ('pending','queued') "
        "AND (available_at IS NULL OR available_at<=?) "
        "AND (dispatched_at IS NULL OR dispatched_at<?) ORDER BY created_at,id LIMIT ?",
        (org_id, timestamp, cutoff, min(max(limit, 1), 100)),
    )
    if not rows:
        return {"dispatched": 0}
    messages = [
        {
            "body": json.dumps(
                {
                    "version": 1,
                    "org_id": org_id,
                    "event_id": row["id"],
                    "generation": int(row["generation"]),
                }
            ),
            "contentType": "text",
        }
        for row in rows
    ]
    # Only mark after send resolves. A crash/ambiguous send can duplicate IDs;
    # the D1 inbox/command receipt owns idempotency, not the queue transport.
    await env.EVENTS.sendBatch(messages)
    timestamp = now().isoformat()
    await db.batch(
        [
            Statement(
                "UPDATE cf_outbox SET dispatched_at=?,delivery_state='queued',"
                "dispatch_attempts=dispatch_attempts+1 WHERE id=? AND completed_at IS NULL "
                "AND delivery_state!='dead' AND delivery_generation=CAST(? AS INTEGER)",
                (timestamp, row["id"], row["generation"]),
            )
            for row in rows
        ]
    )
    return {"dispatched": len(rows)}


async def dispatch_best_effort(env, *, limit=25):
    try:
        return await dispatch(env, limit=limit)
    except Exception as exc:
        logger.error(
            json.dumps({"event": "outbox_dispatch_failed", "error_type": type(exc).__name__})
        )
        return {"dispatched": 0, "error": "dispatch_failed"}


async def record_failure(env, event_id, generation, code, *, final=False):
    timestamp = now().isoformat()
    await D1Database(env.DB).batch(
        [
            Statement(
                "UPDATE cf_outbox SET failed_attempts=failed_attempts+1,last_error=?,"
                "delivery_state=CASE WHEN CAST(? AS INTEGER)=1 THEN 'dead' ELSE delivery_state END,"
                "dead_at=CASE WHEN CAST(? AS INTEGER)=1 THEN ? ELSE dead_at END "
                "WHERE id=? AND json_extract(payload_json,'$.org_id')=? AND completed_at IS NULL "
                "AND delivery_generation=CAST(? AS INTEGER)",
                (
                    code,
                    "1" if final else "0",
                    "1" if final else "0",
                    timestamp,
                    event_id,
                    str(env.ORG_ID),
                    str(generation),
                ),
            )
        ]
    )


async def quarantine(env, message):
    raw, event_id, generation = plain(message.body), None, 0
    digest = hashlib.sha256(str(raw).encode(errors="replace")).hexdigest()
    try:
        body = decode_message(raw, env.ORG_ID)
    except JobFailed:
        event, reason = None, "invalid_message"
    else:
        candidate = str(body.event_id)
        generation = body.generation
        event = await D1Database(env.DB).first(
            "SELECT id,completed_at,last_error FROM cf_outbox WHERE id=? "
            "AND json_extract(payload_json,'$.org_id')=?",
            (candidate, str(env.ORG_ID)),
        )
        event_id = event["id"] if event else None
        reason = event["last_error"] or "retries_exhausted" if event else "event_not_found"
    key = hashlib.sha256(
        json.dumps([str(env.ORG_ID), event_id or str(message.id), generation]).encode()
    ).hexdigest()
    timestamp = now().isoformat()
    state = "resolved" if event and event["completed_at"] else "quarantined"
    statements = [
        Statement(
            "INSERT INTO cf_dead_letters(id,org_id,event_id,provider_message_id,"
            "delivery_generation,body_hash,reason,state,created_at,last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (
                key,
                str(env.ORG_ID),
                event_id,
                str(message.id),
                generation,
                digest,
                reason,
                state,
                timestamp,
                timestamp,
            ),
        )
    ]
    if event_id:
        statements.append(
            Statement(
                "UPDATE cf_outbox SET delivery_state='dead',dead_at=? WHERE id=? "
                "AND completed_at IS NULL AND delivery_generation=CAST(? AS INTEGER)",
                (timestamp, event_id, str(generation)),
            )
        )
        statements.append(
            Statement(
                "UPDATE cf_dead_letters SET state='resolved' WHERE id=? AND "
                "EXISTS(SELECT 1 FROM cf_outbox o WHERE o.id=cf_dead_letters.event_id "
                "AND (o.completed_at IS NOT NULL OR "
                "o.delivery_generation!=cf_dead_letters.delivery_generation)) "
                "AND state!='replayed'",
                (key,),
            )
        )
    await D1Database(env.DB).batch(statements)


async def consume(env, batch):
    dead_queue = str(getattr(env, "DEAD_LETTER_QUEUE_NAME", ""))
    retries = int(getattr(env, "QUEUE_MAX_RETRIES", "3"))
    for message in batch.messages:
        event_id = None
        try:
            if str(batch.queue) == dead_queue:
                await quarantine(env, message)
            else:
                if str(batch.queue) != str(getattr(env, "EVENT_QUEUE_NAME", "")):
                    raise JobFailed("wrong_queue")
                body = decode_message(plain(message.body), env.ORG_ID)
                event_id = str(body.event_id)
                event = await D1Database(env.DB).first(
                    "SELECT id,event_type,payload_json,completed_at,available_at,"
                    "CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox "
                    "WHERE id=? AND json_extract(payload_json,'$.org_id')=?",
                    (event_id, str(env.ORG_ID)),
                )
                if event and event["available_at"] and event["available_at"] > now().isoformat():
                    # D1 owns the future wake-up; do not burn Queue retries while
                    # waiting for tomorrow's quota. Cron dispatches it when due.
                    message.ack()
                    continue
                if event and event["event_type"] == "ai.product.requested":
                    from erp_cf.ai_jobs import process_ai_event

                    await process_ai_event(env, body, event)
                else:
                    await system(
                        env,
                        "jobs.event",
                        f"event:{event_id}:{body.generation}",
                        {"event_id": event_id, "generation": body.generation},
                    )
            message.ack()
        except Exception as exc:
            code = exc.code if isinstance(exc, JobFailed) else "consumer_failed"
            if code == "ai_budget_exceeded" and event_id and exc.defer_until:
                try:
                    available = datetime.fromtimestamp(exc.defer_until / 1000, UTC).isoformat()
                    await D1Database(env.DB).batch(
                        [
                            Statement(
                                "UPDATE cf_outbox SET delivery_state='pending',available_at=?,"
                                "dispatched_at=NULL,last_error='ai_budget_exceeded',"
                                "failed_attempts=failed_attempts+1 WHERE id=? "
                                "AND json_extract(payload_json,'$.org_id')=? "
                                "AND completed_at IS NULL "
                                "AND delivery_generation=CAST(? AS INTEGER)",
                                (available, event_id, str(env.ORG_ID), str(body.generation)),
                            )
                        ]
                    )
                    message.ack()
                    continue
                except Exception:
                    # Do not acknowledge until the durable defer record exists.
                    code = "ai_defer_failed"
            if event_id:
                try:
                    await record_failure(
                        env,
                        event_id,
                        body.generation,
                        code,
                        final=int(message.attempts) > retries,
                    )
                except Exception:
                    logger.error(
                        json.dumps({"event": "failure_record_failed", "event_id": event_id})
                    )
            logger.error(
                json.dumps(
                    {
                        "event": "queue_message_failed",
                        "message_id": str(message.id),
                        "event_id": event_id,
                        "code": code,
                    }
                )
            )
            message.retry({"delaySeconds": min(60, 5 * 2 ** min(int(message.attempts) - 1, 4))})
    await dispatch_best_effort(env)


async def recover_dead_letters(env, *, limit=25):
    """D1 remains recoverable even if a DLQ consumer exhausts its own retries."""
    rows = await D1Database(env.DB).all(
        "SELECT id,CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox o "
        "WHERE json_extract(payload_json,'$.org_id')=? AND completed_at IS NULL "
        "AND delivery_state='dead' AND NOT EXISTS(SELECT 1 FROM cf_dead_letters d "
        "WHERE d.event_id=o.id AND d.delivery_generation=o.delivery_generation) "
        "ORDER BY created_at,id LIMIT ?",
        (str(env.ORG_ID), min(max(limit, 1), 100)),
    )
    for row in rows:
        await quarantine(
            env,
            SimpleNamespace(
                id=f"outbox-sweep:{row['id']}:{row['generation']}",
                body=json.dumps(
                    {
                        "version": 1,
                        "org_id": str(env.ORG_ID),
                        "event_id": row["id"],
                        "generation": int(row["generation"]),
                    }
                ),
            ),
        )
    return {"recovered": len(rows)}


async def run_maintenance(env, kind, run_key, on_date, *, pages=5):
    db, last = D1Database(env.DB), {"status": "running"}
    for _ in range(pages):
        row = await db.first(
            "SELECT status,CAST(version AS TEXT) AS version,CAST(processed AS TEXT) AS processed "
            "FROM cf_job_runs WHERE org_id=? AND kind=? AND run_key=?",
            (str(env.ORG_ID), kind, run_key),
        )
        if row and row["status"] == "complete":
            return {"status": "complete", "processed": int(row["processed"])}
        version = row["version"] if row else "0"
        payload = {
            "kind": kind,
            "run_key": run_key,
            "on_date": on_date.isoformat(),
            "version": version,
        }
        key = hashlib.sha256(json.dumps([kind, run_key, version]).encode()).hexdigest()
        last = await system(env, "jobs.maintenance", "maintenance:" + key, payload)
        if last["status"] == "complete":
            return last
    return last


async def tick(env, scheduled_ms, *, force=False, run_key=None):
    if str(getattr(env, "JOBS_ENABLED", "false")) != "true":
        return {"status": "disabled"}
    scheduled = datetime.fromtimestamp(int(scheduled_ms) / 1000, UTC)
    local = business_time(env, scheduled_ms)
    db, timestamp, org_id = D1Database(env.DB), now().isoformat(), str(env.ORG_ID)
    await db.batch(
        [
            Statement(
                "INSERT INTO cf_scheduler_state(org_id,last_trigger_at) VALUES (?,?) "
                "ON CONFLICT(org_id) DO UPDATE SET last_trigger_at=excluded.last_trigger_at",
                (org_id, scheduled.isoformat()),
            )
        ]
    )
    try:
        result = {"dispatch": await dispatch(env), "quarantine": await recover_dead_letters(env)}
        minute = local.hour * 60 + local.minute
        for kind, due in [("quotes", 5), ("audit", 3 * 60 + 15), ("overdue", 7 * 60)]:
            if force or minute >= due:
                result[kind] = await run_maintenance(
                    env, kind, run_key or local.date().isoformat(), local.date()
                )
        await db.batch(
            [
                Statement(
                    "UPDATE cf_scheduler_state SET last_success_at=?,last_error=NULL "
                    "WHERE org_id=?",
                    (timestamp, org_id),
                )
            ]
        )
        return result
    except Exception as exc:
        await db.batch(
            [
                Statement(
                    "UPDATE cf_scheduler_state SET last_error=? WHERE org_id=?",
                    (type(exc).__name__, org_id),
                )
            ]
        )
        raise
