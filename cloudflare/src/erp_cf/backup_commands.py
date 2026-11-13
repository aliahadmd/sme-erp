"""Authorized, atomic requests; snapshot I/O runs outside financial commands."""

import uuid

from erp_cf.db import Statement, assertion
from erp_cf.errors import ConflictError, PermissionDeniedError
from erp_cf.store import now


async def request_backup(plan, actor, payload):
    if actor is not None and not actor.is_superuser:
        raise PermissionDeniedError("Only administrators may create full database backups")
    key = payload.get("run_key") or "manual:" + uuid.uuid4().hex
    existing = await plan.db.first(
        "SELECT id,status FROM platform_backup_runs WHERE org_id=? AND run_key=?",
        (plan.org_id, key),
    )
    if existing:
        return existing
    identity, timestamp = str(uuid.uuid7()), now().isoformat()
    plan.statements.append(
        Statement(
            "INSERT INTO platform_backup_runs(id,org_id,run_key,status,created_at,updated_at) "
            "VALUES (?,?,?,'requested',?,?)",
            (identity, plan.org_id, key, timestamp, timestamp),
        )
    )
    plan.audit("request", "system.backup", identity)
    return {"id": identity, "status": "requested"}


async def request_restore(plan, actor, payload):
    if not actor.is_superuser:
        raise PermissionDeniedError("Only administrators may restore full database backups")
    backup = await plan.db.first(
        "SELECT id,status FROM platform_backup_runs WHERE id=? AND org_id=?",
        (payload["backup_id"], plan.org_id),
    )
    if not backup or backup["status"] != "complete":
        raise ConflictError("Only a committed, available backup can be restored")
    plan.statements.append(
        assertion(
            plan.identity,
            "EXISTS(SELECT 1 FROM platform_backup_runs WHERE id=? AND status='complete')",
            (backup["id"],),
        )
    )
    identity, timestamp = str(uuid.uuid7()), now().isoformat()
    plan.statements.append(
        Statement(
            "INSERT INTO platform_restore_runs(id,org_id,backup_id,target_database_id,"
            "status,created_at,updated_at) "
            "VALUES (?,?,?,?,'requested',?,?)",
            (
                identity,
                plan.org_id,
                backup["id"],
                payload["target_database_id"],
                timestamp,
                timestamp,
            ),
        )
    )
    plan.audit("request", "system.restore", identity, after={"backup_id": backup["id"]})
    return {"id": identity, "status": "requested"}


async def scheduled_backup(plan, env, payload):
    return await request_backup(plan, None, payload)


COMMANDS = {
    "backups.request": ("core.settings.update", 202, request_backup),
    "backups.restore": ("core.settings.update", 202, request_restore),
}
SYSTEM_COMMANDS = {"backups.schedule": scheduled_backup}
