import uuid
from datetime import date

from app.modules.accounting.schemas import (
    AccountIn,
    AccountOut,
    AccountUpdateIn,
    JournalEntryOut,
    JournalEntryPage,
    LedgerRow,
    ManualEntryIn,
    TrialBalanceRow,
)
from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Request

from erp_cf.accounting_reads import JournalReader
from erp_cf.command_bus import invoke
from erp_cf.core_api import repository
from erp_cf.db import D1Database
from erp_cf.errors import NotFoundError
from erp_cf.identity import current_actor, environment, require

router = APIRouter(prefix="/api/accounting", dependencies=[Depends(current_actor)])


async def reader(request):
    env = environment(request)
    return await JournalReader(D1Database(env.DB), str(env.ORG_ID)).snapshot()


@router.get(
    "/accounts",
    response_model=list[AccountOut],
    dependencies=[Depends(require("accounting.account.read"))],
)
async def accounts(request: Request):
    return await repository(request, "accounting_accounts").find(order_by="code")


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountIn, request: Request):
    return await invoke(request, "accounting.account.create", {"body": body})


@router.patch("/accounts/{identity}", response_model=AccountOut)
async def update_account(identity: uuid.UUID, body: AccountUpdateIn, request: Request):
    return await invoke(
        request,
        "accounting.account.update",
        {"id": identity, "body": body.model_dump(exclude_unset=True)},
    )


@router.get(
    "/journal-entries",
    response_model=JournalEntryPage,
    dependencies=[Depends(require("accounting.entry.read"))],
)
async def entries(params: PageParamsDep, request: Request, source_type: str | None = None):
    return await (await reader(request)).entries(params.limit, params.offset, source_type)


@router.post("/journal-entries", response_model=JournalEntryOut, status_code=201)
async def create_entry(body: ManualEntryIn, request: Request):
    return await invoke(request, "accounting.entry.create", {"body": body})


@router.get(
    "/ledger",
    response_model=list[LedgerRow],
    dependencies=[Depends(require("accounting.entry.read"))],
)
async def ledger(account_id: uuid.UUID, request: Request):
    book = await reader(request)
    if not any(account["id"] == str(account_id) for account in book.accounts):
        raise NotFoundError("Account not found")
    return await book.ledger(account_id)


@router.get(
    "/trial-balance",
    response_model=list[TrialBalanceRow],
    dependencies=[Depends(require("accounting.entry.read"))],
)
async def trial_balance(request: Request, as_of: date | None = None):
    return await (await reader(request)).trial_balance(as_of)
