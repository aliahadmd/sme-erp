"""CRM API schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.money import CurrencyCode
from app.shared.pagination import Page


class ContactIn(BaseModel):
    kind: str = Field("company", pattern=r"^(company|person)$")
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = None
    tax_id: str | None = None
    is_customer: bool = False
    is_supplier: bool = False
    emails: list[dict] = []
    phones: list[dict] = []
    addresses: list[dict] = []
    # Omitted → the organization's base currency.
    currency: CurrencyCode | None = None
    payment_terms_days: int = Field(30, ge=0, le=365)
    credit_limit: Decimal = Field(0, ge=0)
    tags: list[str] = []
    owner_user_id: uuid.UUID | None = None
    notes: str | None = None

    def validate_flags(self) -> None:
        if not self.is_customer and not self.is_supplier:
            raise ValueError("Contact must be a customer and/or a supplier")


class ContactUpdateIn(BaseModel):
    kind: str | None = Field(None, pattern=r"^(company|person)$")
    name: str | None = Field(None, min_length=1, max_length=200)
    legal_name: str | None = None
    tax_id: str | None = None
    is_customer: bool | None = None
    is_supplier: bool | None = None
    emails: list[dict] | None = None
    phones: list[dict] | None = None
    addresses: list[dict] | None = None
    currency: CurrencyCode | None = None
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    credit_limit: Decimal | None = Field(None, ge=0)
    tags: list[str] | None = None
    owner_user_id: uuid.UUID | None = None
    notes: str | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    kind: str
    name: str
    legal_name: str | None
    tax_id: str | None
    is_customer: bool
    is_supplier: bool
    emails: list
    phones: list
    addresses: list
    currency: str
    payment_terms_days: int
    credit_limit: Decimal
    tags: list
    owner_user_id: uuid.UUID | None
    notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime


ContactPage = Page[ContactOut]


class ContactArchiveIn(BaseModel):
    archived: bool = True
