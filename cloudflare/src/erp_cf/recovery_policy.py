"""Restored external effects need deliberate review, including old reminders."""

from datetime import datetime


async def mail_held(plan, *, source_id=None, overdue=False, sticky=False, released=False):
    policy = await plan.db.first(
        "SELECT hold_external,restored_at FROM platform_recovery_policy WHERE singleton=1"
    )
    if sticky or policy and policy["hold_external"]:
        return True
    if policy and overdue and not released and source_id:
        invoice = await plan.repository("invoicing_invoices").get(source_id, required=False)
        if invoice and invoice["created_at"] <= datetime.fromisoformat(policy["restored_at"]):
            return True
    return False
