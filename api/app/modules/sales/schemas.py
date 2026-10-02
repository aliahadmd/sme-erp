"""Sales API schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.money import CurrencyCode
from app.shared.pagination import Page


class OrderLineIn(BaseModel):
    product_id: uuid.UUID | None = None
    description: str | None = None
    qty: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(None, ge=0)
    discount_pct: Decimal = Field(0, ge=0, le=100)
    tax_id: uuid.UUID | None = None


class OrderCreateIn(BaseModel):
    customer_id: uuid.UUID
    order_date: date | None = None
    expected_date: date | None = None
    # Omitted → the party's default currency, else the org base currency.
    currency: CurrencyCode | None = None
    notes: str | None = None
    lines: list[OrderLineIn] = []


class OrderUpdateIn(BaseModel):
    customer_id: uuid.UUID | None = None
    order_date: date | None = None
    expected_date: date | None = None
    currency: CurrencyCode | None = None
    notes: str | None = None
    lines: list[OrderLineIn] | None = None


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    product_id: uuid.UUID | None
    product_name: str | None
    description: str | None
    qty: Decimal
    uom_code: str | None
    unit_price: Decimal
    discount_pct: Decimal
    tax_id: uuid.UUID | None
    tax_rate_pct: Decimal
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal
    qty_delivered: Decimal = 0
    qty_invoiced: Decimal = 0


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    customer_id: uuid.UUID | None
    customer_name: str | None
    order_date: date
    expected_date: date | None
    currency: str
    status: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    notes: str | None
    created_by: uuid.UUID | None
    confirmed_at: date | None
    confirmed_by: uuid.UUID | None
    created_at: datetime | None = None
    lines: list[OrderLineOut] = []


OrderPage = Page[OrderOut]
