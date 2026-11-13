"""Chart-of-account writes and balanced manual journal commands."""

from datetime import UTC, datetime

from app.modules.accounting.schemas import (
    AccountIn,
    AccountOut,
    AccountUpdateIn,
    JournalEntryOut,
    ManualEntryIn,
)

from erp_cf.accounting import Ledger
from erp_cf.errors import ValidationError


async def create_account(plan, actor, payload):
    body = AccountIn.model_validate(payload["body"])
    if await plan.repository("accounting_accounts").first("code=?", (body.code,)):
        raise ValidationError(f"Account code {body.code} already exists")
    account = plan.add("accounting_accounts", body.model_dump())
    plan.audit("create", "accounting.account", account["id"], after={"code": account["code"]})
    return AccountOut.model_validate(account)


async def update_account(plan, actor, payload):
    body = AccountUpdateIn.model_validate(payload["body"])
    account = await plan.repository("accounting_accounts").get(payload["id"])
    if account["is_system"] and body.is_active is False:
        raise ValidationError(
            "System accounts receive automatic postings and cannot be deactivated"
        )
    before = {k: account[k] for k in ("name", "is_active")}
    account = plan.update("accounting_accounts", account, body.model_dump(exclude_unset=True))
    plan.audit(
        "update",
        "accounting.account",
        account["id"],
        before=before,
        after={k: account[k] for k in before},
    )
    return AccountOut.model_validate(account)


async def manual_entry(plan, actor, payload):
    body = ManualEntryIn.model_validate(payload["body"])
    ledger = Ledger(plan)
    lines = [
        (await ledger.get_account(line.account_id), line.debit, line.credit) for line in body.lines
    ]
    entry = await ledger.post(
        entry_date=body.entry_date or datetime.now(UTC).date(),
        memo=body.memo,
        source_type="manual",
        lines=lines,
    )
    plan.audit("create", "accounting.entry", entry["id"], after={"number": entry["number"]})
    return JournalEntryOut.model_validate(entry)


COMMANDS = {
    "accounting.account.create": ("accounting.account.create", 201, create_account),
    "accounting.account.update": ("accounting.account.update", 200, update_account),
    "accounting.entry.create": ("accounting.entry.create", 201, manual_entry),
}
