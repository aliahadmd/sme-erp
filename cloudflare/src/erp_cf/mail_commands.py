"""D1 mail claims, outcome receipts and deliberate operator reconciliation."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.shared.notification_text import party_email

from erp_cf.business_clock import business_time
from erp_cf.db import Statement, assertion
from erp_cf.errors import ConflictError, NotFoundError, ValidationError
from erp_cf.job_commands import complete_event
from erp_cf.mail_provider import (
    PERMANENT,
    THROTTLED,
    build_message,
    mode,
    native_payload,
    payload_hash,
)
from erp_cf.recovery_policy import mail_held
from erp_cf.settlement import SETTLEABLE, balance
from erp_cf.store import now


async def load(plan, payload):
    event = await plan.db.first(
        "SELECT id,event_type,payload_json,completed_at,"
        "CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox "
        "WHERE id=? AND json_extract(payload_json,'$.org_id')=?",
        (payload["event_id"], plan.org_id),
    )
    if not event or event["event_type"] != "mail.delivery.requested":
        raise NotFoundError("Mail event not found")
    data = json.loads(event["payload_json"])
    row = await plan.db.first(
        "SELECT id,org_id,recipient,user_id,preference,subject,body,kind,source_id,status,"
        "provider_id,delivery_event_id,attempt_id,claim_until,CAST(version AS TEXT) AS version "
        "FROM cf_mail_outbox WHERE id=? AND org_id=?",
        (data["mail_id"], plan.org_id),
    )
    if not row:
        raise NotFoundError("Mail intent not found")
    return event, row


def guard(plan, row):
    plan.statements.append(
        assertion(
            plan.identity,
            "EXISTS(SELECT 1 FROM cf_mail_outbox WHERE id=? AND org_id=? "
            "AND version=CAST(? AS INTEGER))",
            (row["id"], plan.org_id, row["version"]),
        )
    )


async def eligible(plan, env, row):
    if row["kind"] not in {"staff", "customer", "manual", "overdue"} or not row["source_id"]:
        return "invalid_source"
    if row["kind"] == "staff":
        user = await plan.repository("core_users").get(row["user_id"], required=False)
        permissions = {
            "invoice_sent": "invoicing.invoice.read",
            "overdue_reminder": "invoicing.invoice.read",
            "payment_received": "invoicing.payment.read",
        }
        permission = permissions.get(row["preference"])
        if not user or not user["is_active"] or not permission:
            return "recipient_inactive"
        current = party_email(SimpleNamespace(emails=[user["email"]]))
        if current != row["recipient"] or not user["email_prefs"].get(row["preference"], True):
            return "recipient_preferences_changed"
        predicate = (
            "EXISTS(SELECT 1 FROM core_users u WHERE u.id=? AND u.is_active=1 AND "
            "(u.is_superuser=1 OR EXISTS(SELECT 1 FROM core_user_roles ur "
            "JOIN core_role_permissions rp ON rp.role_id=ur.role_id "
            "JOIN core_permissions p ON p.id=rp.permission_id "
            "WHERE ur.user_id=u.id AND p.code=?)))"
        )
        permitted = await plan.db.first(
            "SELECT " + predicate + " AS allowed", (user["id"], permission)
        )
        if not permitted["allowed"]:
            return "recipient_permissions_changed"
        plan.guard("core_users", user)
        plan.statements.append(assertion(plan.identity, predicate, (user["id"], permission)))
        if row["preference"] == "payment_received":
            payment = await plan.repository("invoicing_payments").get(
                row["source_id"], required=False
            )
            if not payment or payment["status"] != "recorded":
                return "stale_source"
            plan.guard("invoicing_payments", payment)
            return None
    invoice = await plan.repository("invoicing_invoices").get(row["source_id"], required=False)
    if not invoice or invoice["status"] not in SETTLEABLE:
        return "stale_source"
    plan.guard("invoicing_invoices", invoice)
    overdue = row["kind"] == "overdue" or row["preference"] == "overdue_reminder"
    if overdue and (
        invoice["invoice_type"] not in {"ar", "ap"}
        or balance(invoice) <= 0
        or not invoice["due_date"]
        or invoice["due_date"] >= business_time(env).date()
    ):
        return "no_longer_overdue"
    if overdue and row["kind"] == "staff":
        row["body"] = (
            f"Open balance {balance(invoice)} {invoice['currency']} since {invoice['due_date']}"
        )
    if row["kind"] in {"customer", "manual", "overdue"}:
        party = await plan.repository("crm_contacts").get(invoice["party_id"], required=False)
        current = party_email(SimpleNamespace(**party)) if party else None
        if not current or current != row["recipient"]:
            return "recipient_changed"
        plan.guard("crm_contacts", party)
        if row["kind"] == "overdue":
            row["body"] = (
                f"Hello {invoice['party_name'] or ''},\n\nInvoice {invoice['number']} "
                f"was due on {invoice['due_date']}. The open balance is "
                f"{balance(invoice)} {invoice['currency']}.\n\n"
                "If you have already paid, please disregard this reminder."
            )
    return None


def terminal(plan, event, row, status, reason=None):
    guard(plan, row)
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_outbox SET status=?,last_error=?,completed_at=?,claim_until=NULL,"
            "version=version+1 WHERE id=?",
            (status, reason, None if status == "uncertain" else now().isoformat(), row["id"]),
        )
    )
    complete_event(plan, event, "mail_" + status)
    return {"action": "complete", "status": status}


async def prepare(plan, env, payload):
    event, row = await load(plan, payload)
    if event["completed_at"] or int(event["generation"]) != payload["generation"]:
        return {"action": "complete", "status": "already_processed"}
    if row["delivery_event_id"] != event["id"]:
        complete_event(plan, event, "mail_superseded")
        return {"action": "complete", "status": "superseded"}
    if row["status"] == "uncertain":
        if row["claim_until"] and row["claim_until"] > now().isoformat():
            return {"action": "busy"}
        plan.statements.append(
            Statement(
                "UPDATE cf_mail_attempts SET state='uncertain',error_code='send_outcome_unknown',"
                "finished_at=? WHERE id=? AND state='prepared'",
                (now().isoformat(), row["attempt_id"]),
            )
        )
        return terminal(plan, event, row, "uncertain", "send_outcome_unknown")
    if row["status"] != "pending":
        complete_event(plan, event, "mail_" + row["status"])
        return {"action": "complete", "status": row["status"]}
    current_mode = mode(env)
    if current_mode == "disabled":
        return terminal(plan, event, row, "disabled")
    reason = await eligible(plan, env, row)
    if reason:
        return terminal(plan, event, row, "cancelled", reason)
    event_data = json.loads(event["payload_json"])
    if await mail_held(
        plan,
        source_id=row["source_id"],
        overdue=row["kind"] == "overdue" or row["preference"] == "overdue_reminder",
        sticky=bool(event_data.get("recovery_hold")),
        released=bool(event_data.get("recovery_release")),
    ):
        return terminal(plan, event, row, "uncertain", "recovery_review_required")
    message = build_message(env, row)
    if current_mode == "test":
        native_payload(message)
    if current_mode == "live" and not hasattr(env, "EMAIL"):
        from erp_cf.errors import DomainError

        raise DomainError("Email binding is not configured", code="email_unavailable", status=503)
    attempt, timestamp = payload["attempt_id"], now()
    uuid.UUID(attempt)
    guard(plan, row)
    plan.statements.append(
        assertion(
            plan.identity,
            "EXISTS(SELECT 1 FROM cf_outbox WHERE id=? AND completed_at IS NULL "
            "AND delivery_generation=CAST(? AS INTEGER))",
            (event["id"], event["generation"]),
        )
    )
    plan.statements.append(
        Statement(
            "INSERT INTO cf_mail_attempts(id,org_id,mail_id,event_id,generation,state,payload_hash,"
            "created_at,finished_at) VALUES (?,?,?,?,CAST(? AS INTEGER),?,?,?,?)",
            (
                attempt,
                plan.org_id,
                row["id"],
                event["id"],
                event["generation"],
                "test" if current_mode == "test" else "prepared",
                payload_hash(message),
                timestamp.isoformat(),
                timestamp.isoformat() if current_mode == "test" else None,
            ),
        )
    )
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_outbox SET status=?,attempt_id=?,claim_until=?,subject=?,body=?,"
            "version=version+1,completed_at=? WHERE id=?",
            (
                "test" if current_mode == "test" else "uncertain",
                attempt,
                None
                if current_mode == "test"
                else (timestamp + timedelta(seconds=120)).isoformat(),
                message["subject"],
                row["body"],
                timestamp.isoformat() if current_mode == "test" else None,
                row["id"],
            ),
        )
    )
    if current_mode == "test":
        complete_event(plan, event, "mail_test")
        return {"action": "complete", "status": "test"}
    return {"action": "send", "attempt_id": attempt, "mail_id": row["id"], "message": message}


async def finish(plan, env, payload):
    event, row = await load(plan, payload)
    attempt = await plan.db.first(
        "SELECT id,state FROM cf_mail_attempts WHERE id=? AND org_id=? AND mail_id=? "
        "AND event_id=? AND generation=CAST(? AS INTEGER)",
        (payload["attempt_id"], plan.org_id, row["id"], event["id"], str(payload["generation"])),
    )
    if not attempt or attempt["state"] not in {"prepared", "uncertain"}:
        return {"outcome": "obsolete"}
    state, provider, code = (
        payload["result"]["state"],
        payload["result"].get("provider_id"),
        payload["result"].get("code"),
    )
    if state not in {"accepted", "rejected", "uncertain"}:
        raise ValidationError("Invalid mail outcome")
    if state == "accepted" and (not isinstance(provider, str) or not 1 <= len(provider) <= 256):
        raise ValidationError("Missing mail provider ID")
    if (
        state == "rejected"
        and code not in PERMANENT | THROTTLED
        or state == "uncertain"
        and code != "send_outcome_unknown"
        or state == "accepted"
        and code is not None
    ):
        raise ValidationError("Invalid mail outcome code")
    if state != "accepted" and provider is not None:
        raise ValidationError("Only accepted mail has a provider ID")
    plan.statements.append(
        assertion(
            plan.identity,
            "EXISTS(SELECT 1 FROM cf_mail_attempts WHERE id=? AND state=?)",
            (attempt["id"], attempt["state"]),
        )
    )
    timestamp = now().isoformat()
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_attempts SET state=?,provider_id=?,error_code=?,finished_at=? "
            "WHERE id=?",
            (state, provider, code, timestamp, attempt["id"]),
        )
    )
    # A slow provider can respond after the lease expires or an operator acts.
    # Preserve its receipt without overwriting a replacement delivery decision.
    if (
        row["delivery_event_id"] != event["id"]
        or row["attempt_id"] != attempt["id"]
        or int(event["generation"]) != payload["generation"]
        or row["status"] != "uncertain"
    ):
        return {"outcome": "obsolete", "receipt_recorded": True}
    guard(plan, row)
    if not event["completed_at"]:
        plan.statements.append(
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM cf_outbox WHERE id=? AND completed_at IS NULL "
                "AND delivery_generation=CAST(? AS INTEGER))",
                (event["id"], event["generation"]),
            )
        )
    if state == "rejected" and code in THROTTLED and not event["completed_at"]:
        delay = 3600 if code == "E_DAILY_LIMIT_EXCEEDED" else 300
        available = (now() + timedelta(seconds=delay)).isoformat()
        plan.statements.extend(
            [
                Statement(
                    "UPDATE cf_mail_outbox SET status='pending',attempt_id=NULL,claim_until=NULL,"
                    "last_error=?,version=version+1 WHERE id=?",
                    (code, row["id"]),
                ),
                Statement(
                    "UPDATE cf_outbox SET delivery_state='pending',available_at=?,"
                    "dispatched_at=NULL,"
                    "last_error=?,failed_attempts=failed_attempts+1 WHERE id=?",
                    (available, code, event["id"]),
                ),
            ]
        )
        return {"outcome": "deferred"}
    status = {"accepted": "sent", "rejected": "dead", "uncertain": "uncertain"}[state]
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_outbox SET status=?,provider_id=?,last_error=?,claim_until=NULL,"
            "completed_at=?,version=version+1 WHERE id=?",
            (status, provider, code, None if status == "uncertain" else timestamp, row["id"]),
        )
    )
    if not event["completed_at"]:
        complete_event(plan, event, "mail_" + status)
    return {"outcome": status}


async def resolve(plan, actor, payload):
    row = await plan.db.first(
        "SELECT id,status,attempt_id,claim_until,CAST(version AS TEXT) AS version "
        "FROM cf_mail_outbox WHERE id=? AND org_id=?",
        (payload["id"], plan.org_id),
    )
    if not row:
        raise NotFoundError("Mail intent not found")
    if row["status"] not in {"uncertain", "dead"}:
        raise ConflictError("Only rejected or uncertain mail may be reconciled")
    if row["claim_until"] and row["claim_until"] > now().isoformat():
        raise ConflictError("The mail attempt is still in progress")
    body = payload["body"]
    action, reason = body.get("action"), body.get("reason", "")
    if (
        not isinstance(action, str)
        or action not in {"retry", "mark_sent", "cancel"}
        or not isinstance(reason, str)
        or not 5 <= len(reason) <= 500
    ):
        raise ValidationError("Choose a mail action and a reason of 5-500 characters")
    if (
        action == "retry"
        and row["status"] == "uncertain"
        and body.get("allow_possible_duplicate") is not True
    ):
        raise ValidationError("An uncertain retry must acknowledge possible duplicate delivery")
    provider = body.get("provider_id")
    if action == "mark_sent" and (not isinstance(provider, str) or not 1 <= len(provider) <= 256):
        raise ValidationError("A provider ID is required to confirm acceptance")
    guard(plan, row)
    # A crashed attempt whose lease has elapsed is still an unknown send.
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_attempts SET state='uncertain',error_code='send_outcome_unknown',"
            "finished_at=? WHERE id=? AND state='prepared'",
            (now().isoformat(), row["attempt_id"]),
        )
    )
    status = {"retry": "pending", "mark_sent": "sent", "cancel": "cancelled"}[action]
    delivery = (
        plan.emit("mail.delivery.requested", {"mail_id": row["id"], "recovery_release": True})
        if action == "retry"
        else None
    )
    plan.statements.append(
        Statement(
            "UPDATE cf_mail_outbox SET status=?,delivery_event_id=coalesce(?,delivery_event_id),"
            "attempt_id=NULL,claim_until=NULL,provider_id=?,last_error=NULL,completed_at=?,"
            "version=version+1 WHERE id=?",
            (
                status,
                delivery,
                provider if action == "mark_sent" else None,
                None if action == "retry" else now().isoformat(),
                row["id"],
            ),
        )
    )
    plan.audit(
        "reconcile",
        "system.mail",
        row["id"],
        after={
            "action": action,
            "reason": reason,
            "possible_duplicate": body.get("allow_possible_duplicate", False),
        },
    )
    return {"status": status}


SYSTEM_COMMANDS = {"mail.prepare": prepare, "mail.finish": finish}
COMMANDS = {"mail.resolve": ("core.settings.update", 200, resolve)}
