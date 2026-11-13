"""Portable reporting response contracts. Monetary results are base currency."""

import uuid
from decimal import Decimal

from pydantic import BaseModel


class WeekPoint(BaseModel):
    week_start: str
    sales: Decimal
    purchases: Decimal


class DashboardOut(BaseModel):
    sales_mtd: Decimal
    purchases_mtd: Decimal
    open_ar: Decimal
    open_ap: Decimal
    low_stock_count: int
    weeks: list[WeekPoint]


class ByCustomerRow(BaseModel):
    party_id: uuid.UUID | None
    party_name: str
    invoice_count: int
    total: Decimal


class AgingRow(BaseModel):
    bucket: str
    amount: Decimal


class StockValuationRow(BaseModel):
    warehouse_code: str
    warehouse_name: str
    products: int
    qty_on_hand: Decimal
    value: Decimal


class TaxSummaryRow(BaseModel):
    direction: str
    tax_code: str | None
    tax_name: str | None
    rate_pct: Decimal | None
    net: Decimal
    tax: Decimal
