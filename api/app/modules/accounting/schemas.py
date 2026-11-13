"""Portable accounting contracts shared by server and Workers APIs."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


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
    debit: Decimal = Field(Decimal("0"), ge=0)
    credit: Decimal = Field(Decimal("0"), ge=0)


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
