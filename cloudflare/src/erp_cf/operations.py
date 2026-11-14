"""Read-only operational assessment and redacted Cron heartbeat for native Monitors."""

import json
import math
from dataclasses import dataclass, fields
from datetime import UTC, datetime

from erp_cf.backup_format import MAX_MANIFEST_BYTES, attribute
from erp_cf.backup_state import manifest_key, prefix
from erp_cf.db import D1Database, Statement, plain
from erp_cf.maintenance import state as maintenance_state


@dataclass(frozen=True)
class Limits:
    database_budget_bytes: int = 10_000_000_000
    outbox_age_seconds: int = 900
    scheduler_age_seconds: int = 900
    backup_age_seconds: int = 26 * 3600
    backup_stall_seconds: int = 600
    restore_stall_seconds: int = 1800

    @classmethod
    def from_env(cls, env):
        values = {
            field.name: int(getattr(env, "OPS_" + field.name.upper(), field.default))
            for field in fields(cls)
        }
        if any(value <= 0 or value > 2**53 - 1 for value in values.values()):
            raise ValueError("Invalid operational threshold")
        return cls(**values)


def now():
    return datetime.now(UTC)


def age(value, checked):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Operational timestamps must include a timezone")
    if (parsed - checked).total_seconds() > 60:
        raise ValueError("Operational timestamp is in the future")
    return max(0, (checked - parsed).total_seconds())


async def snapshot(env, checked):
    db, org, timestamp = D1Database(env.DB), str(env.ORG_ID), checked.isoformat()
    results = await db.batch(
        [
            Statement("SELECT 1"),
            Statement(
                "SELECT delivery_state,CAST(COUNT(*) AS TEXT) AS n,MIN(created_at) AS oldest "
                "FROM cf_outbox WHERE json_extract(payload_json,'$.org_id')=? "
                "AND completed_at IS NULL AND (delivery_state='dead' OR "
                "available_at IS NULL OR julianday(available_at)<=julianday(?)) "
                "GROUP BY delivery_state",
                (org, timestamp),
            ),
            Statement(
                "SELECT CAST(COUNT(*) AS TEXT) AS n FROM cf_dead_letters "
                "WHERE org_id=? AND state='quarantined'",
                (org,),
            ),
            Statement(
                "SELECT status,CAST(COUNT(*) AS TEXT) AS n,MIN(created_at) AS oldest "
                "FROM cf_mail_outbox WHERE org_id=? AND (status='dead' OR "
                "(status='uncertain' AND (claim_until IS NULL OR "
                "julianday(claim_until)<=julianday(?))) OR status='pending') GROUP BY status",
                (org, timestamp),
            ),
            Statement(
                "SELECT last_success_at,last_error FROM cf_scheduler_state WHERE org_id=?", (org,)
            ),
            Statement(
                "SELECT id,org_id,CAST(fence AS TEXT) AS fence,manifest_hash,completed_at "
                "FROM platform_backup_runs WHERE org_id=? AND status='complete' "
                "ORDER BY completed_at DESC LIMIT 1",
                (org,),
            ),
            Statement(
                "SELECT id,status,created_at,capture_started_at,updated_at "
                "FROM platform_backup_runs "
                "WHERE org_id=? AND status IN ('requested','capturing','publishing') "
                "ORDER BY CASE status WHEN 'capturing' THEN 0 WHEN 'publishing' THEN 1 ELSE 2 END,"
                "created_at LIMIT 1",
                (org,),
            ),
            Statement(
                "SELECT id,updated_at FROM platform_backup_runs WHERE org_id=? AND status='failed' "
                "ORDER BY updated_at DESC LIMIT 1",
                (org,),
            ),
            Statement(
                "SELECT id,status,updated_at FROM platform_restore_runs WHERE org_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (org,),
            ),
        ]
    )
    data = [[plain(row) for row in result["results"]] for result in results]
    return {
        "database_bytes": plain(results[0].get("meta", {})).get("size_after"),
        "outbox": data[1],
        "quarantined": int(data[2][0]["n"]),
        "mail": data[3],
        "scheduler": data[4][0] if data[4] else None,
        "backup": data[5][0] if data[5] else None,
        "backup_active": data[6][0] if data[6] else None,
        "backup_failed": data[7][0] if data[7] else None,
        "restore": data[8][0] if data[8] else None,
        "maintenance": await maintenance_state(db),
    }


async def assess(env, data, checked, limits):
    issues, metrics = [], {}

    def issue(code, severity="critical", **details):
        issues.append({"code": code, "severity": severity, **details})

    def timed(code, value, limit, *, severity="critical", **details):
        elapsed = age(value, checked)
        metrics[code + "_age_seconds"] = elapsed
        if elapsed is None or elapsed > limit:
            issue(code, severity, **details)
        return elapsed

    size = data["database_bytes"]
    if (
        type(size) not in {int, float}
        or not math.isfinite(size)
        or not 0 < size <= 2**53 - 1
        or int(size) != size
    ):
        size = None
    else:
        size = int(size)
    metrics["database_bytes"] = size
    metrics["database_budget_bytes"] = limits.database_budget_bytes
    if size is None:
        issue("database_size_unavailable")
    elif size * 100 >= limits.database_budget_bytes * 90:
        issue("database_headroom")
    elif size * 100 >= limits.database_budget_bytes * 80:
        issue("database_headroom", "warning")

    active = data["backup_active"]
    paused = bool(data["maintenance"]["active"])
    metrics["maintenance_active"] = paused
    pause_expected = False
    if active:
        identity = active["id"]
        elapsed = timed(
            "backup_stalled", active["updated_at"], limits.backup_stall_seconds, backup_id=identity
        )
        if paused:
            capture_age = age(active["capture_started_at"] or active["created_at"], checked)
            pause_budget = int(getattr(env, "BACKUP_MAX_PAUSE_SECONDS", "600"))
            if pause_budget <= 0:
                raise ValueError("Invalid backup pause budget")
            pause_expected = capture_age is not None and capture_age <= pause_budget + 60
        metrics["active_backup_id"] = identity
        metrics["active_backup_age_seconds"] = elapsed
    if paused and not pause_expected:
        issue("maintenance_overdue")

    if str(getattr(env, "JOBS_ENABLED", "false")) != "true":
        issue("jobs_disabled")
    elif not pause_expected:
        scheduler = data["scheduler"]
        timed(
            "scheduler_stale",
            scheduler["last_success_at"] if scheduler else None,
            limits.scheduler_age_seconds,
        )
        if scheduler and scheduler["last_error"]:
            issue("scheduler_failed")
        for row in data["outbox"]:
            state, count = row["delivery_state"], int(row["n"])
            metrics["outbox_" + state] = count
            if state == "dead" and count:
                issue("outbox_dead", "warning", count=count)
            elif state in {"pending", "queued"} and count:
                timed(
                    "outbox_" + state + "_stale",
                    row["oldest"],
                    limits.outbox_age_seconds,
                    count=count,
                )
    metrics["quarantined"] = data["quarantined"]
    if data["quarantined"]:
        issue("queue_quarantined", "warning", count=data["quarantined"])
    for row in data["mail"]:
        state, count = row["status"], int(row["n"])
        metrics["mail_" + state] = count
        if state in {"dead", "uncertain"} and count:
            issue("mail_" + state, "warning", count=count)
        elif (
            state == "pending"
            and str(getattr(env, "EMAIL_MODE", "disabled")) == "live"
            and not paused
        ):
            timed("mail_pending_stale", row["oldest"], limits.outbox_age_seconds, count=count)

    if str(getattr(env, "BACKUPS_ENABLED", "false")) != "true":
        issue("backups_disabled")
    else:
        backup = data["backup"]
        timed(
            "backup_overdue", backup["completed_at"] if backup else None, limits.backup_age_seconds
        )
        if backup:
            metrics["last_backup_id"] = backup["id"]
            failed = data["backup_failed"]
            if failed and age(failed["updated_at"], checked) < age(backup["completed_at"], checked):
                issue("backup_failed", "warning", backup_id=failed["id"])
            keys = [
                f"commits/{env.ORG_ID}/{backup['id']}.json",
                prefix(backup) + "COMMITTED.json",
                manifest_key(backup),
            ]
            try:
                for key in keys:
                    obj = await env.BACKUPS.head(key)
                    bound = MAX_MANIFEST_BYTES if "/manifest-" in key else 4096
                    if obj is None or not 0 < int(attribute(obj, "size")) <= bound:
                        issue("backup_artifact_missing", backup_id=backup["id"])
                        break
                else:
                    metrics["backup_objects_present"] = True
            except Exception:
                issue("backup_artifacts_unavailable", backup_id=backup["id"])
    restore = data["restore"]
    if restore and restore["status"] == "failed":
        issue("restore_failed", "warning", restore_id=restore["id"])
    elif restore and restore["status"] in {"requested", "restoring"}:
        timed(
            "restore_stalled",
            restore["updated_at"],
            limits.restore_stall_seconds,
            restore_id=restore["id"],
        )
    status = (
        "critical"
        if any(i["severity"] == "critical" for i in issues)
        else ("warning" if issues else "maintenance" if paused else "ok")
    )
    return {
        "status": status,
        "checked_at": checked.isoformat(),
        "issues": issues,
        "metrics": metrics,
    }


async def health(env):
    checked = now()
    if str(getattr(env, "OPS_ENABLED", "false")) != "true":
        return {
            "status": "disabled",
            "checked_at": checked.isoformat(),
            "issues": [],
            "metrics": {},
        }
    try:
        limits = Limits.from_env(env)
        return await assess(env, await snapshot(env, checked), checked, limits)
    except Exception as exc:
        return {
            "status": "unavailable",
            "checked_at": checked.isoformat(),
            "issues": [
                {
                    "code": "telemetry_unavailable",
                    "severity": "critical",
                    "error_type": type(exc).__name__,
                }
            ],
            "metrics": {},
        }


async def emit_health(env):
    report = await health(env)
    if report["status"] != "disabled":
        # Fixed event/status fields allow native SQL Monitors to assess health
        # and missing heartbeats independently of this Cron invocation.
        # Do not log recipients, payloads, SQL, credentials or exception text.
        print(
            json.dumps(
                {
                    "event": "erp_operational_health",
                    "message": "erp_operational_health:" + report["status"],
                    "level": "info",
                    "version": 1,
                    "org_id": str(env.ORG_ID),
                    **report,
                },
                separators=(",", ":"),
            )
        )
    return report
