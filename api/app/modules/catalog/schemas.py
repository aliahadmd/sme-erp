"""Catalog API schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.pagination import Page


# ------------------------------------------------------------------ products
class ProductCategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None


class ProductCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str


class UomIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)


class UomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


class TaxIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    rate_pct: Decimal = Field(ge=0, le=100)
    applies_to: str = Field("both", pattern=r"^(sale|purchase|both)$")
    is_default_sale: bool = False
    is_default_purchase: bool = False


class TaxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    rate_pct: Decimal
    applies_to: str
    is_default_sale: bool
    is_default_purchase: bool


class ProductIn(BaseModel):
    sku: str | None = Field(None, max_length=50)
    barcode: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    type: str = Field("goods", pattern=r"^(goods|service)$")
    category_id: uuid.UUID | None = None
    uom_id: uuid.UUID | None = None
    is_purchasable: bool = True
    is_sellable: bool = True
    track_inventory: bool = True
    sale_price: Decimal = Field(0, ge=0)
    cost_price: Decimal = Field(0, ge=0)
    sale_tax_id: uuid.UUID | None = None
    purchase_tax_id: uuid.UUID | None = None
    min_stock: Decimal = Field(0, ge=0)
    tags: list[str] = []


class ProductUpdateIn(BaseModel):
    barcode: str | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    type: str | None = Field(None, pattern=r"^(goods|service)$")
    category_id: uuid.UUID | None = None
    uom_id: uuid.UUID | None = None
    is_purchasable: bool | None = None
    is_sellable: bool | None = None
    track_inventory: bool | None = None
    sale_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    sale_tax_id: uuid.UUID | None = None
    purchase_tax_id: uuid.UUID | None = None
    min_stock: Decimal | None = Field(None, ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    barcode: str | None
    name: str
    description: str | None
    type: str
    category_id: uuid.UUID | None
    uom_id: uuid.UUID | None
    is_purchasable: bool
    is_sellable: bool
    track_inventory: bool
    sale_price: Decimal
    cost_price: Decimal
    sale_tax_id: uuid.UUID | None
    purchase_tax_id: uuid.UUID | None
    min_stock: Decimal
    image_key: str | None
    status: str
    created_at: datetime
    updated_at: datetime


ProductPage = Page[ProductOut]
CategoryPage = Page[ProductCategoryOut]
UomOutList = list[UomOut]
TaxOutList = list[TaxOut]
