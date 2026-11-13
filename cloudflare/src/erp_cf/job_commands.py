"""Private system plans and authenticated operational replay commands."""

import json
from datetime import date, timedelta

from erp_cf.db import Statement, assertion
from erp_cf.document_jobs import expire_quotes
from erp_cf.errors import ConflictError, DomainError, NotFoundError, ValidationError
from erp_cf.notifications import Notifications
from erp_cf.store import now

IGNORED_EVENTS = {
    "sales_order.confirmed",
    "purchase_order.confirmed",
    "receipt.posted",
    "receipt.voided",
    "delivery.voided",
    "adjustment.posted",
    "invoice.voided",
    "payment.voided",
    "payment.allocated",
}


async def consume_event(plan, env, payload):
    event = await plan.db.first(
        "SELECT id,event_type,payload_json,completed_at,"
        "CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox WHERE id=? "
        "AND json_extract(payload_json,'$.org_id')=?",
        (payload["event_id"], plan.org_id),
    )
    if not event:
        raise NotFoundError("Outbox event not found")
    if event["completed_at"]:
        return {"outcome": "already_processed"}
    if int(event["generation"]) != payload["generation"]:
        return {"outcome": "superseded"}
    data = json.loads(event["payload_json"])
    notices = Notifications(
        plan, env, event_id=event["id"], recovery_hold=bool(data.get("recovery_hold"))
    )
    kind = event["event_type"]
    if kind == "invoice.posted":
        outcome = await notices.invoice(data)
    elif kind == "payment.recorded":
        outcome = await notices.payment(data)
    elif kind == "delivery.posted":
        outcome = await notices.delivery(data)
    elif kind == "email.requested":
        invoice = await plan.repository("invoicing_invoices").get(data["invoice_id"])
        if invoice["status"] not in {"posted", "partial", "paid"}:
            outcome = "stale_source"
        else:
            plan.guard("invoicing_invoices", invoice)
            await notices.mail(
                data["to"],
                data["subject"],
                data["body"],
                kind="manual",
                source_id=invoice["id"],
                dedupe=f"manual:{event['id']}",
                known_new=True,
            )
            outcome = "processed"
    elif kind == "mail.delivery.requested":
        # External I/O belongs in the queue handler, outside the command lock.
        raise DomainError(
            "Use the mail delivery handler", code="mail_dispatch_required", status=409
        )
    elif kind == "ai.batch.requested":
        from erp_cf.ai_commands import batch_page

        outcome = await batch_page(plan, env, data)
    elif kind in IGNORED_EVENTS:
        outcome = "no_subscriber"
    else:
        raise DomainError("Unsupported outbox event", code="unsupported_event", status=422)
    complete_event(plan, event, outcome)
    return {"outcome": outcome}


def complete_event(plan, event, outcome):
    timestamp = now().isoformat()
    plan.statements.extend(
        [
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM cf_outbox WHERE id=? AND completed_at IS NULL "
                "AND delivery_generation=CAST(? AS INTEGER))",
                (event["id"], event["generation"]),
            ),
            Statement(
                "INSERT INTO cf_event_receipts(event_id,org_id,outcome,processed_at) "
                "VALUES (?,?,?,?)",
                (event["id"], plan.org_id, outcome, timestamp),
            ),
            Statement(
                "UPDATE cf_outbox SET completed_at=?,delivery_state='complete',"
                "last_error=NULL,dead_at=NULL,available_at=NULL WHERE id=?",
                (timestamp, event["id"]),
            ),
            Statement(
                "UPDATE cf_dead_letters SET state='resolved',last_seen_at=? "
                "WHERE org_id=? AND event_id=?",
                (timestamp, plan.org_id, event["id"]),
            ),
        ]
    )


async def maintenance(plan, env, payload):
    kind, run_key = payload["kind"], payload["run_key"]
    on_date = date.fromisoformat(payload["on_date"])
    row = await plan.db.first(
        "SELECT org_id,kind,run_key,on_date,cursor,status,CAST(version AS TEXT) AS version,"
        "CAST(processed AS TEXT) AS processed,started_at,updated_at,finished_at "
        "FROM cf_job_runs WHERE org_id=? AND kind=? AND run_key=?",
        (plan.org_id, kind, run_key),
    )
    if row and row["status"] == "complete":
        return {"status": "complete", "processed": int(row["processed"])}
    expected = int(payload["version"])
    if (int(row["version"]) if row else 0) != expected:
        raise ConflictError("Maintenance cursor changed")
    cursor, count, more, timestamp = row["cursor"] if row else "", 0, False, now().isoformat()
    if kind == "quotes":
        result = await expire_quotes(plan, on_date, after=cursor, limit=25)
        cursor, count, more = result["cursor"], result["expired"], result["has_more"]
    elif kind == "overdue":
        rows = await plan.repository("invoicing_invoices").find(
            "invoice_type IN ('ar','ap') AND status IN ('posted','partial') "
            "AND due_date<? AND id>?",
            (on_date.isoformat(), cursor),
            order_by="id",
            limit=25,
        )
        notices = Notifications(plan, env)
        for invoice in rows:
            await notices.overdue(invoice, on_date=on_date, reminders=True)
        cursor, count, more = (rows[-1]["id"] if rows else cursor), len(rows), len(rows) == 25
    elif kind == "audit":
        days = int(getattr(env, "AUDIT_RETENTION_DAYS", "365"))
        if not 1 <= days <= 36500:
            raise ValidationError("Invalid audit retention")
        cutoff = (on_date - timedelta(days=days)).isoformat()
        rows = await plan.repository("core_audit_logs").find(
            "created_at<? AND id>?",
            (cutoff, cursor),
            order_by="id",
            limit=100,
        )
        for audit in rows:
            plan.delete("core_audit_logs", audit)
        cursor, count, more = (rows[-1]["id"] if rows else cursor), len(rows), len(rows) == 100
    else:
        raise ValidationError("Unknown maintenance job")
    status = "running" if more else "complete"
    if row:
        plan.statements.extend(
            [
                assertion(
                    plan.identity,
                    "EXISTS(SELECT 1 FROM cf_job_runs WHERE org_id=? AND kind=? "
                    "AND run_key=? AND version=?)",
                    (plan.org_id, kind, run_key, expected),
                ),
                Statement(
                    "UPDATE cf_job_runs SET cursor=?,status=?,version=version+1,"
                    "processed=processed+?,updated_at=?,finished_at=? "
                    "WHERE org_id=? AND kind=? AND run_key=?",
                    (
                        cursor,
                        status,
                        count,
                        timestamp,
                        timestamp if not more else None,
                        plan.org_id,
                        kind,
                        run_key,
                    ),
                ),
            ]
        )
    else:
        plan.statements.append(
            Statement(
                "INSERT INTO cf_job_runs(org_id,kind,run_key,on_date,cursor,status,"
                "version,processed,started_at,updated_at,finished_at) "
                "VALUES (?,?,?,?,?,?,1,?,?,?,?)",
                (
                    plan.org_id,
                    kind,
                    run_key,
                    on_date.isoformat(),
                    cursor,
                    status,
                    count,
                    timestamp,
                    timestamp,
                    timestamp if not more else None,
                ),
            )
        )
    return {
        "status": status,
        "processed": count + (int(row["processed"]) if row else 0),
        "cursor": cursor,
    }


async def replay_dead_letter(plan, actor, payload):
    row = await plan.db.first(
        "SELECT id,event_id,state,CAST(delivery_generation AS TEXT) AS generation "
        "FROM cf_dead_letters WHERE id=? AND org_id=?",
        (payload["id"], plan.org_id),
    )
    if not row:
        raise NotFoundError("Dead letter not found")
    if row["state"] != "quarantined" or not row["event_id"]:
        raise ConflictError("This dead letter cannot be replayed")
    timestamp = now().isoformat()
    plan.statements.extend(
        [
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM cf_dead_letters WHERE id=? AND org_id=? "
                "AND state='quarantined') AND EXISTS(SELECT 1 FROM cf_outbox "
                "WHERE id=? AND completed_at IS NULL "
                "AND delivery_generation=CAST(? AS INTEGER))",
                (row["id"], plan.org_id, row["event_id"], row["generation"]),
            ),
            Statement(
                "UPDATE cf_outbox SET delivery_state='pending',dispatched_at=NULL,"
                "last_error=NULL,dead_at=NULL,available_at=NULL,failed_attempts=0,"
                "delivery_generation=delivery_generation+1 WHERE id=?",
                (row["event_id"],),
            ),
            Statement(
                "UPDATE cf_dead_letters SET state='replayed',replayed_at=? WHERE id=?",
                (timestamp, row["id"]),
            ),
        ]
    )
    plan.audit("replay", "system.dead_letter", row["id"], after={"event_id": row["event_id"]})
    return {"status": "queued", "event_id": row["event_id"]}


SYSTEM_COMMANDS = {"jobs.event": consume_event, "jobs.maintenance": maintenance}
COMMANDS = {"jobs.dead_letter.replay": ("core.settings.update", 200, replay_dead_letter)}
