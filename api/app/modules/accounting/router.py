"""Accounting API: chart of accounts, journal entries, ledger, trial balance."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationError
from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.accounting.service import post_entry
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/accounting")


# ------------------------------------------------------------------ schemas
class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    type: str
    is_system: bool
    is_active: bool


class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(pattern=r"^(asset|liability|equity|income|expense)$")


class JournalLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_code: str | None = None
    account_name: str | None = None
    debit: Decimal
    credit: Decimal


class JournalEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    entry_date: date
    memo: str | None
    source_type: str
    source_id: uuid.UUID | None
    lines: list[JournalLineOut] = []


class ManualLineIn(BaseModel):
    account_id: uuid.UUID
    debit: Decimal = Field(0, ge=0)
    credit: Decimal = Field(0, ge=0)


class ManualEntryIn(BaseModel):
    entry_date: date | None = None
    memo: str = Field(min_length=1)
    lines: list[ManualLineIn] = []


class LedgerRow(BaseModel):
    entry_number: str
    entry_date: date
    memo: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal


class AccountUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    is_active: bool | None = None


class JournalEntryPage(BaseModel):
    items: list[JournalEntryOut]
    total: int
    limit: int
    offset: int


class TrialBalanceRow(BaseModel):
    account_id: uuid.UUID
    code: str
    name: str
    type: str
    total_debit: Decimal
    total_credit: Decimal
    balance: Decimal


# --------------------------------------------------------------- endpoints
@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    _user: CurrentUser = Depends(require("accounting.account.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Account]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(Account).where(Account.org_id == org.id).order_by(Account.code)
        )
    )


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountIn,
    user: CurrentUser = Depends(require("accounting.account.create")),
    session: AsyncSession = Depends(get_session),
) -> Account:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(Account).where(Account.org_id == org.id, Account.code == body.code)
        )
    ).first()
    if existing:
        raise ValidationError(f"Account code {body.code} already exists")
    account = Account(org_id=org.id, code=body.code, name=body.name, type=body.type)
    session.add(account)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="accounting.account",
        entity_id=account.id,
        after={"code": account.code},
    )
    await session.commit()
    await session.refresh(account)
    return account


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID,
    body: AccountUpdateIn,
    user: CurrentUser = Depends(require("accounting.account.update")),
    session: AsyncSession = Depends(get_session),
) -> Account:
    org = await get_organization(session)
    account = await session.get(Account, account_id)
    if not account or account.org_id != org.id:
        raise NotFoundError("Account not found")
    if account.is_system and body.is_active is False:
        raise ValidationError(
            "System accounts receive automatic postings and cannot be deactivated"
        )
    before = {"name": account.name, "is_active": account.is_active}
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="accounting.account",
        entity_id=account.id,
        before=before,
        after={"name": account.name, "is_active": account.is_active},
    )
    await session.commit()
    await session.refresh(account)
    return account


@router.get("/journal-entries", response_model=JournalEntryPage)
async def list_journal_entries(
    params: PageParamsDep,
    source_type: str | None = None,
    _user: CurrentUser = Depends(require("accounting.entry.read")),
    session: AsyncSession = Depends(get_session),
) -> JournalEntryPage:
    org = await get_organization(session)
    stmt = (
        select(JournalEntry)
        .where(JournalEntry.org_id == org.id)
        .options(selectinload(JournalEntry.lines).selectinload(JournalLine.account))
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.created_at.desc())
    )
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    rows, total = await paginate(session, stmt, params)
    out = []
    for entry in rows:
        item = JournalEntryOut.model_validate(entry)
        for i, line in enumerate(entry.lines):
            item.lines[i].account_code = line.account.code
            item.lines[i].account_name = line.account.name
        out.append(item)
    return JournalEntryPage(items=out, total=total, limit=params.limit, offset=params.offset)


@router.post("/journal-entries", response_model=JournalEntryOut, status_code=201)
async def create_manual_entry(
    body: ManualEntryIn,
    user: CurrentUser = Depends(require("accounting.entry.create")),
    session: AsyncSession = Depends(get_session),
) -> JournalEntryOut:
    org = await get_organization(session)
    accounts: dict[uuid.UUID, Account] = {}
    lines = []
    for line in body.lines:
        account = accounts.get(line.account_id) or await session.get(Account, line.account_id)
        if not account or account.org_id != org.id:
            raise ValidationError("Unknown account in entry")
        if not account.is_active:
            raise ValidationError(f"Account {account.code} is inactive")
        accounts[line.account_id] = account
        lines.append((account, line.debit, line.credit))
    entry = await post_entry(
        session,
        org.id,
        entry_date=body.entry_date or date.today(),
        memo=body.memo,
        source_type="manual",
        lines=lines,
        actor_id=user.id,
    )
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="accounting.entry",
        entity_id=entry.id,
        after={"number": entry.number},
    )
    await session.commit()
    await session.refresh(entry)
    return JournalEntryOut.model_validate(entry)


@router.get("/ledger", response_model=list[LedgerRow])
async def account_ledger(
    account_id: uuid.UUID,
    _user: CurrentUser = Depends(require("accounting.entry.read")),
    session: AsyncSession = Depends(get_session),
) -> list[LedgerRow]:
    org = await get_organization(session)
    account = await session.get(Account, account_id)
    if not account or account.org_id != org.id:
        raise NotFoundError("Account not found")
    stmt = (
        select(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == account_id, JournalEntry.org_id == org.id)
        .options(selectinload(JournalLine.entry))
        .order_by(JournalEntry.entry_date, JournalEntry.created_at)
    )
    lines = (await session.scalars(stmt)).all()
    rows: list[LedgerRow] = []
    balance = Decimal("0")
    for line in lines:
        balance += Decimal(str(line.debit)) - Decimal(str(line.credit))
        rows.append(
            LedgerRow(
                entry_number=line.entry.number,
                entry_date=line.entry.entry_date,
                memo=line.entry.memo,
                debit=line.debit,
                credit=line.credit,
                balance=balance.quantize(Decimal("0.01")),
            )
        )
    return rows


@router.get("/trial-balance", response_model=list[TrialBalanceRow])
async def trial_balance(
    as_of: date | None = None,
    _user: CurrentUser = Depends(require("accounting.entry.read")),
    session: AsyncSession = Depends(get_session),
) -> list[TrialBalanceRow]:
    """Per-account totals aggregated in the database (optionally up to and
    including `as_of`)."""
    org = await get_organization(session)
    totals_stmt = (
        select(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.org_id == org.id, JournalEntry.status == "posted")
        .group_by(JournalLine.account_id)
    )
    if as_of:
        totals_stmt = totals_stmt.where(JournalEntry.entry_date <= as_of)
    totals = {
        account_id: (Decimal(str(debit)), Decimal(str(credit)))
        for account_id, debit, credit in (await session.execute(totals_stmt)).all()
    }
    accounts = (
        await session.scalars(
            select(Account).where(Account.org_id == org.id).order_by(Account.code)
        )
    ).all()
    rows = []
    for account in accounts:
        debit, credit = totals.get(account.id, (Decimal("0"), Decimal("0")))
        rows.append(
            TrialBalanceRow(
                account_id=account.id,
                code=account.code,
                name=account.name,
                type=account.type,
                total_debit=debit,
                total_credit=credit,
                balance=(debit - credit).quantize(Decimal("0.01")),
            )
        )
    return rows
