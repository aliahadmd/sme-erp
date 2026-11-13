"""Independent backup controller; D1 versions/leases survive object restarts."""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from erp_cf import backup_capture, backup_restore
from erp_cf.backup_format import BackupError, attribute
from erp_cf.backup_state import checkpoint, load, prefix
from erp_cf.db import D1Database, Statement, advance_business_epoch, assertion
from erp_cf.errors import ConflictError, DomainError
from erp_cf.maintenance import LeaseLost, state
from erp_cf.store import now

logger = logging.getLogger("erp.backups")


class BackupController:
    def __init__(self, env):
        self.env, self.lock = env, asyncio.Lock()

    async def advance(self, *, steps=25):
        if str(getattr(self.env, "BACKUPS_ENABLED", "false")) != "true":
            return {"status": "disabled", "paused": False}
        db, bucket = D1Database(self.env.DB), self.env.BACKUPS
        manifests = {}
        async with self.lock:
            for _ in range(min(max(steps, 1), 100)):
                selected = await db.first(
                    "SELECT id FROM platform_backup_runs WHERE org_id=? AND status IN "
                    "('requested','capturing','publishing','deleting') "
                    "ORDER BY created_at,id LIMIT 1",
                    (str(self.env.ORG_ID),),
                )
                table = "platform_backup_runs"
                if not selected:
                    selected = await db.first(
                        "SELECT id FROM platform_restore_runs WHERE org_id=? "
                        "AND status IN ('requested','restoring') ORDER BY created_at,id LIMIT 1",
                        (str(self.env.ORG_ID),),
                    )
                    table = "platform_restore_runs"
                if not selected:
                    return {"status": "idle", "paused": bool((await state(db))["active"])}
                row = await load(db, table, selected["id"])
                try:
                    if table == "platform_backup_runs":
                        if row["status"] == "requested":
                            await backup_capture.start(db, row)
                        elif row["status"] == "capturing":
                            maximum = int(getattr(self.env, "BACKUP_MAX_PAUSE_SECONDS", "600"))
                            if not 60 <= maximum <= 3600:
                                raise BackupError(
                                    "Invalid maximum backup pause", code="backup_config"
                                )
                            started = datetime.fromisoformat(row["capture_started_at"])
                            if (now() - started).total_seconds() > maximum:
                                raise BackupError(
                                    "Backup exceeded its maximum write pause", code="backup_timeout"
                                )
                            await backup_capture.capture_page(db, bucket, row)
                        elif row["status"] == "publishing":
                            await backup_capture.publish(db, bucket, row)
                        elif row["status"] == "deleting":
                            await self.delete_page(db, bucket, row)
                    else:
                        if str(getattr(self.env, "RESTORE_ENABLED", "false")) != "true":
                            raise BackupError("Isolated recovery is disabled")
                        target = D1Database(self.env.RESTORE_DB)
                        if row["target_database_id"] != str(
                            getattr(self.env, "RESTORE_DATABASE_ID", "")
                        ):
                            raise BackupError("Recovery database binding changed during this run")
                        backup = await load(db, "platform_backup_runs", row["backup_id"])
                        if backup["status"] != "complete":
                            raise BackupError("The source archive is no longer available")
                        identity = (backup["id"], backup["manifest_hash"])
                        if identity not in manifests:
                            manifests[identity] = await backup_capture.committed(bucket, backup)
                        manifest = manifests[identity]
                        if row["status"] == "requested":
                            await backup_restore.initialize(
                                db, target, bucket, row, backup, manifest
                            )
                        else:
                            await backup_restore.step(db, target, bucket, row, backup, manifest)
                except LeaseLost:
                    await self.fail(db, table, row, "maintenance_lease_lost")
                    break
                except BackupError as exc:
                    await self.fail(db, table, row, exc.code + ": " + exc.detail)
                    break
                except ConflictError:
                    # Busy admission or another controller won a checkpoint.
                    break
                except Exception as exc:
                    logger.error(
                        json.dumps(
                            {
                                "event": "backup_step_failed",
                                "job_id": row["id"],
                                "error_type": type(exc).__name__,
                            }
                        )
                    )
                    # Keep the last durable cursor. An I/O retry uses the same
                    # page key/target receipt; an expired capture will fail closed.
                    from contextlib import suppress

                    with suppress(ConflictError):
                        await checkpoint(
                            db, table, row, {"last_error": "backup_io_failed:" + type(exc).__name__}
                        )
                    break
            return {"status": "working", "paused": bool((await state(db))["active"])}

    async def fail(self, db, table, row, code):
        statements = []
        if table == "platform_backup_runs":
            held = await db.first(
                "SELECT owner,CAST(fence AS TEXT) AS fence FROM platform_maintenance "
                "WHERE singleton=1"
            )
            if held and held["owner"] == row["id"]:
                statements.append(
                    Statement(
                        "UPDATE platform_maintenance SET owner=NULL,purpose=NULL,expires_at=NULL "
                        "WHERE singleton=1 AND owner=? AND fence=CAST(? AS INTEGER)",
                        (row["id"], held["fence"]),
                    )
                )
        from contextlib import suppress

        with suppress(ConflictError):
            await checkpoint(
                db,
                table,
                row,
                {"status": "failed", "last_error": code},
                statements=statements,
            )

    async def retention(self):
        db = D1Database(self.env.DB)
        days = int(getattr(self.env, "BACKUP_RETENTION_DAYS", "35"))
        if not 7 <= days <= 3650:
            raise DomainError("Invalid backup retention", code="backup_config", status=503)
        cutoff = (now() - timedelta(days=days)).isoformat()
        row = await db.first(
            "SELECT id FROM platform_backup_runs b WHERE org_id=? "
            "AND status IN ('complete','failed') "
            "AND coalesce(completed_at,capture_started_at,created_at)<? "
            "AND NOT EXISTS(SELECT 1 FROM platform_restore_runs r WHERE r.backup_id=b.id "
            "AND r.status IN ('requested','restoring')) ORDER BY created_at LIMIT 1",
            (str(self.env.ORG_ID), cutoff),
        )
        if row:
            run = await load(db, "platform_backup_runs", row["id"])
            await checkpoint(
                db,
                "platform_backup_runs",
                run,
                {"status": "deleting", "cursor": None},
                statements=[
                    assertion(
                        run["id"],
                        "NOT EXISTS(SELECT 1 FROM platform_restore_runs WHERE backup_id=? "
                        "AND status IN ('requested','restoring'))",
                        (run["id"],),
                    ),
                    Statement("DELETE FROM cf_assertions WHERE command_id=?", (run["id"],)),
                ],
            )

    async def cancel(self, token, identity):
        from erp_cf.backup_archive import administrator
        from erp_cf.store import UnitOfWork

        actor = await administrator(self.env, token)
        db = D1Database(self.env.DB)
        async with self.lock:
            row = await load(db, "platform_backup_runs", identity)
            if row["org_id"] != str(self.env.ORG_ID):
                raise BackupError("Backup not found")
            if row["status"] == "failed" and row["last_error"] == "cancelled":
                return {"id": identity, "status": "failed"}
            if row["status"] not in {"requested", "capturing"}:
                raise ConflictError("Only an unsealed backup may be cancelled")
            plan = UnitOfWork(
                db, str(self.env.ORG_ID), actor.id, "cancel:" + identity, "backups.cancel", {}
            )
            actor.guard(plan)
            statements = [
                *plan.statements,
                Statement(
                    "UPDATE platform_maintenance SET owner=NULL,purpose=NULL,expires_at=NULL "
                    "WHERE owner=? AND fence=CAST(? AS INTEGER)",
                    (identity, row["fence"]),
                ),
            ]
            plan.statements.clear()
            plan.audit("cancel", "system.backup", identity)
            statements.extend(
                [
                    *plan.statements,
                    advance_business_epoch(str(self.env.ORG_ID)),
                    Statement("DELETE FROM cf_assertions WHERE command_id=?", (plan.identity,)),
                ]
            )
            await checkpoint(
                db,
                "platform_backup_runs",
                row,
                {"status": "failed", "last_error": "cancelled"},
                statements=statements,
            )
            return {"id": identity, "status": "failed"}

    async def import_backup(self, token, identity, key):
        from erp_cf.backup_archive import import_backup

        return await import_backup(self.env, token, identity, key)

    async def delete_page(self, db, bucket, row):
        # Remove the recoverable marker before any referenced pages. Source job
        # metadata retains deletion state through restarts and network loss.
        await bucket.delete(
            [prefix(row) + "COMMITTED.json", f"commits/{row['org_id']}/{row['id']}.json"]
        )
        options = {"prefix": prefix(row), "limit": 100}
        if row["cursor"]:
            options["cursor"] = row["cursor"]
        listed = await bucket.list(options)
        objects = attribute(listed, "objects")
        if objects:
            await bucket.delete([attribute(item, "key") for item in objects])
        if attribute(listed, "truncated"):
            await checkpoint(
                db, "platform_backup_runs", row, {"cursor": attribute(listed, "cursor")}
            )
            return
        await checkpoint(
            db,
            "platform_backup_runs",
            row,
            {"status": "deleted", "schema_json": None},
            statements=[
                Statement("DELETE FROM platform_backup_pages WHERE backup_id=?", (row["id"],)),
            ],
        )
