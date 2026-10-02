"""Inventory API schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.shared.pagination import Page


class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    is_default: bool = False


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_default: bool


class StockRowOut(BaseModel):
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    qty_on_hand: Decimal
    avg_cost: Decimal
    stock_value: Decimal
    is_low: bool


class ReceiptLineIn(BaseModel):
    product_id: uuid.UUID
    qty: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(0, ge=0)


class ReceiptIn(BaseModel):
    warehouse_id: uuid.UUID | None = None
    source_po_id: uuid.UUID | None = None
    notes: str | None = None
    lines: list[ReceiptLineIn] = []


class ReceiptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    qty: Decimal
    unit_cost: Decimal


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    warehouse_id: uuid.UUID
    source_type: str | None
    source_id: uuid.UUID | None
    source_number: str | None
    status: str
    notes: str | None
    posted_at: datetime | None
    lines: list[ReceiptLineOut] = []


class DeliveryLineIn(BaseModel):
    product_id: uuid.UUID
    qty: Decimal = Field(gt=0)


class DeliveryIn(BaseModel):
    warehouse_id: uuid.UUID | None = None
    source_so_id: uuid.UUID | None = None
    notes: str | None = None
    lines: list[DeliveryLineIn] = []


class DeliveryLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    qty: Decimal


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    warehouse_id: uuid.UUID
    source_type: str | None
    source_id: uuid.UUID | None
    source_number: str | None
    status: str
    notes: str | None
    posted_at: datetime | None
    lines: list[DeliveryLineOut] = []


class AdjustmentLineIn(BaseModel):
    product_id: uuid.UUID
    qty: Decimal  # may be negative, never zero
    # Base-currency unit cost for FOUND stock (positive lines). Omitted/0 →
    # the current moving-average cost. Ignored for decreases (valued at avg).
    unit_cost: Decimal = Field(0, ge=0)

    @field_validator("qty")
    @classmethod
    def _non_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("Adjustment quantity cannot be zero")
        return value


class AdjustmentIn(BaseModel):
    warehouse_id: uuid.UUID | None = None
    reason: str = Field("stock_correction", pattern=r"^(stock_correction|damage|count|other)$")
    notes: str | None = None
    lines: list[AdjustmentLineIn] = []


class AdjustmentLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    qty: Decimal


class AdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    warehouse_id: uuid.UUID
    reason: str
    notes: str | None
    status: str
    posted_at: datetime | None
    lines: list[AdjustmentLineOut] = []


class StockMoveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty: Decimal
    move_type: str
    ref_type: str | None
    ref_number: str | None
    unit_cost: Decimal
    cogs: Decimal
    moved_at: datetime


MovePage = Page[StockMoveOut]
ReceiptPage = Page[ReceiptOut]
DeliveryPage = Page[DeliveryOut]
AdjustmentPage = Page[AdjustmentOut]
