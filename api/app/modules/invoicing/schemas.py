"""Invoicing API schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.pagination import Page


class InvoiceLineIn(BaseModel):
    product_id: uuid.UUID | None = None
    description: str | None = None
    qty: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(None, ge=0)
    discount_pct: Decimal = Field(0, ge=0, le=100)
    tax_id: uuid.UUID | None = None


class InvoiceCreateIn(BaseModel):
    invoice_type: str = Field("ar", pattern=r"^(ar|ap|ar_credit|ap_credit)$")
    original_invoice_id: uuid.UUID | None = None
    party_id: uuid.UUID
    source_order_id: uuid.UUID | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = Field("USD", min_length=3, max_length=3)
    notes: str | None = None
    lines: list[InvoiceLineIn] = []


class PaymentAllocateIn(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class PaymentIn(BaseModel):
    direction: str = Field("in", pattern=r"^(in|out)$")
    party_id: uuid.UUID
    payment_date: date | None = None
    amount: Decimal = Field(gt=0)
    method: str = Field("bank", pattern=r"^(cash|bank|card|transfer|other)$")
    reference: str | None = None
    notes: str | None = None
    credit_note_id: uuid.UUID | None = None
    allocations: list[PaymentAllocateIn] = []


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    product_id: uuid.UUID | None
    product_name: str | None
    description: str | None
    qty: Decimal
    unit_price: Decimal
    discount_pct: Decimal
    tax_id: uuid.UUID | None
    tax_rate_pct: Decimal
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_type: str
    number: str | None
    party_id: uuid.UUID | None
    party_name: str | None
    source_type: str | None
    source_id: uuid.UUID | None
    source_number: str | None
    invoice_date: date
    due_date: date | None
    currency: str
    status: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    notes: str | None
    posted_at: datetime | None
    lines: list[InvoiceLineOut] = []


class PaymentAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    amount: Decimal


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    direction: str
    party_id: uuid.UUID
    party_name: str | None
    payment_date: date
    amount: Decimal
    method: str
    reference: str | None
    notes: str | None
    status: str
    credit_note_id: uuid.UUID | None = None
    allocations: list[PaymentAllocationOut] = []


InvoicePage = Page[InvoiceOut]
PaymentPage = Page[PaymentOut]


class StatementLine(BaseModel):
    invoice_id: uuid.UUID
    number: str | None
    invoice_date: date
    due_date: date | None
    total: Decimal
    amount_paid: Decimal
    balance: Decimal
    status: str


class StatementOut(BaseModel):
    party_id: uuid.UUID
    party_name: str | None
    open_balance: Decimal
    invoices: list[StatementLine]
