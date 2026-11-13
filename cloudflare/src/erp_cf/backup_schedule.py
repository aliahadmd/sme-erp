"""Cron drives bounded backup work before ordinary writable maintenance jobs."""

from erp_cf.background import system
from erp_cf.backup_rpc import call as rpc
from erp_cf.business_clock import business_time


async def run(env, scheduled_ms):
    if str(getattr(env, "BACKUPS_ENABLED", "false")) != "true":
        return False
    control = env.BACKUP_CONTROL.getByName(str(env.ORG_ID))
    progress = await rpc(control, "advance")
    if progress["paused"]:
        return True
    if str(getattr(env, "BACKUP_SCHEDULE_ENABLED", "false")) == "true":
        hour = int(getattr(env, "BACKUP_HOUR_LOCAL", "2"))
        if not 0 <= hour <= 23:
            raise ValueError("Invalid backup hour")
        local = business_time(env, scheduled_ms)
        if local.hour == hour:
            key = "daily:" + local.date().isoformat()
            await system(env, "backups.schedule", "backup:" + key, {"run_key": key})
            progress = await rpc(control, "advance")
        if not progress["paused"]:
            await rpc(control, "retention")
    return bool(progress["paused"])
