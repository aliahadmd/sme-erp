"""Reporting module — read-only aggregates across module schemas.

This is the one sanctioned cross-module reader: it queries other modules'
tables for read models only and never writes outside its own scope (it has
no tables).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization

router = APIRouter(prefix="/reports")


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
    tax_code: str | None
    tax_name: str | None
    rate_pct: Decimal | None
    net: Decimal
    tax: Decimal


def _month_start() -> date:
    today = date.today()
    return today.replace(day=1)


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> DashboardOut:
    org = await get_organization(session)
    month_start = _month_start()

    sales_mtd = (
        await session.scalar(
            text(
                "SELECT COALESCE(SUM(total), 0) FROM invoicing.invoices "
                "WHERE org_id = :org AND invoice_type = 'ar' "
                "AND status IN ('posted','partial','paid') AND invoice_date >= :d"
            ),
            {"org": str(org.id), "d": month_start},
        )
    ) or Decimal("0")
    purchases_mtd = (
        await session.scalar(
            text(
                "SELECT COALESCE(SUM(total), 0) FROM invoicing.invoices "
                "WHERE org_id = :org AND invoice_type = 'ap' "
                "AND status IN ('posted','partial','paid') AND invoice_date >= :d"
            ),
            {"org": str(org.id), "d": month_start},
        )
    ) or Decimal("0")
    open_ar = (
        await session.scalar(
            text(
                "SELECT COALESCE(SUM(total - amount_paid), 0) FROM invoicing.invoices "
                "WHERE org_id = :org AND invoice_type = 'ar' AND status IN ('posted','partial')"
            ),
            {"org": str(org.id)},
        )
    ) or Decimal("0")
    open_ap = (
        await session.scalar(
            text(
                "SELECT COALESCE(SUM(total - amount_paid), 0) FROM invoicing.invoices "
                "WHERE org_id = :org AND invoice_type = 'ap' AND status IN ('posted','partial')"
            ),
            {"org": str(org.id)},
        )
    ) or Decimal("0")
    low_stock = (
        await session.scalar(
            text(
                """
                SELECT COUNT(*) FROM inventory.stock s
                JOIN catalog.products p ON p.id = s.product_id
                WHERE s.org_id = :org AND s.qty_on_hand > 0 AND s.qty_on_hand <= p.min_stock
                """
            ),
            {"org": str(org.id)},
        )
    ) or 0

    # 12-week sales/purchases buckets
    today = date.today()
    start = today - timedelta(days=today.weekday() + 77)  # 12 Mondays back
    rows = (
        await session.execute(
            text(
                """
                SELECT date_trunc('week', invoice_date)::date AS week_start,
                       invoice_type, COALESCE(SUM(total), 0) AS total
                FROM invoicing.invoices
                WHERE org_id = :org AND status IN ('posted','partial','paid')
                  AND invoice_date >= :start
                GROUP BY 1, 2 ORDER BY 1
                """
            ),
            {"org": str(org.id), "start": start},
        )
    ).mappings()
    buckets: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        week = row["week_start"].isoformat()
        buckets.setdefault(week, {"ar": Decimal("0"), "ap": Decimal("0")})
        buckets[week]["ar" if row["invoice_type"] == "ar" else "ap"] += Decimal(str(row["total"]))
    weeks = [
        WeekPoint(
            week_start=(start + timedelta(days=7 * i)).isoformat(),
            sales=buckets.get((start + timedelta(days=7 * i)).isoformat(), {}).get(
                "ar", Decimal("0")
            ),
            purchases=buckets.get((start + timedelta(days=7 * i)).isoformat(), {}).get(
                "ap", Decimal("0")
            ),
        )
        for i in range(13)
    ]

    return DashboardOut(
        sales_mtd=Decimal(str(sales_mtd)),
        purchases_mtd=Decimal(str(purchases_mtd)),
        open_ar=Decimal(str(open_ar)),
        open_ap=Decimal(str(open_ap)),
        low_stock_count=int(low_stock),
        weeks=weeks,
    )


@router.get("/sales-by-customer", response_model=list[ByCustomerRow])
async def sales_by_customer(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[ByCustomerRow]:
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT party_id, COALESCE(MAX(party_name), 'Unknown') AS party_name,
                       COUNT(*) AS invoice_count, COALESCE(SUM(total), 0) AS total
                FROM invoicing.invoices
                WHERE org_id = :org AND invoice_type = 'ar'
                  AND status IN ('posted','partial','paid')
                  AND (:from IS NULL OR invoice_date >= :from)
                  AND (:to IS NULL OR invoice_date <= :to)
                GROUP BY party_id ORDER BY total DESC
                """
            ),
            {"org": str(org.id), "from": date_from, "to": date_to},
        )
    ).mappings()
    return [
        ByCustomerRow(
            party_id=r["party_id"],
            party_name=r["party_name"],
            invoice_count=r["invoice_count"],
            total=Decimal(str(r["total"])),
        )
        for r in rows
    ]


@router.get("/purchases-by-supplier", response_model=list[ByCustomerRow])
async def purchases_by_supplier(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[ByCustomerRow]:
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT party_id, COALESCE(MAX(party_name), 'Unknown') AS party_name,
                       COUNT(*) AS invoice_count, COALESCE(SUM(total), 0) AS total
                FROM invoicing.invoices
                WHERE org_id = :org AND invoice_type = 'ap'
                  AND status IN ('posted','partial','paid')
                  AND (:from IS NULL OR invoice_date >= :from)
                  AND (:to IS NULL OR invoice_date <= :to)
                GROUP BY party_id ORDER BY total DESC
                """
            ),
            {"org": str(org.id), "from": date_from, "to": date_to},
        )
    ).mappings()
    return [
        ByCustomerRow(
            party_id=r["party_id"],
            party_name=r["party_name"],
            invoice_count=r["invoice_count"],
            total=Decimal(str(r["total"])),
        )
        for r in rows
    ]


@router.get("/stock-valuation", response_model=list[StockValuationRow])
async def stock_valuation(
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[StockValuationRow]:
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT w.code AS warehouse_code, w.name AS warehouse_name,
                       COUNT(*) AS products, COALESCE(SUM(s.qty_on_hand), 0) AS qty,
                       COALESCE(SUM(s.qty_on_hand * s.avg_cost), 0) AS value
                FROM inventory.stock s
                JOIN inventory.warehouses w ON w.id = s.warehouse_id
                WHERE s.org_id = :org AND s.qty_on_hand != 0
                GROUP BY w.code, w.name ORDER BY w.code
                """
            ),
            {"org": str(org.id)},
        )
    ).mappings()
    return [
        StockValuationRow(
            warehouse_code=r["warehouse_code"],
            warehouse_name=r["warehouse_name"],
            products=r["products"],
            qty_on_hand=Decimal(str(r["qty"])),
            value=Decimal(str(r["value"])).quantize(Decimal("0.01")),
        )
        for r in rows
    ]


def _bucket(days: int | None) -> str:
    if days is None:
        return "90+"
    if days <= 30:
        return "current"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


@router.get("/aging", response_model=list[AgingRow])
async def aging(
    invoice_type: str = Query("ar", pattern=r"^(ar|ap)$"),
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[AgingRow]:
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT due_date, COALESCE(SUM(total - amount_paid), 0) AS open
                FROM invoicing.invoices
                WHERE org_id = :org AND invoice_type = :t AND status IN ('posted','partial')
                GROUP BY due_date
                """
            ),
            {"org": str(org.id), "t": invoice_type},
        )
    ).mappings()
    buckets = {
        "current": Decimal("0"),
        "31-60": Decimal("0"),
        "61-90": Decimal("0"),
        "90+": Decimal("0"),
    }
    today = date.today()
    for row in rows:
        due = row["due_date"] or today
        days = (today - due).days
        bucket = _bucket(days if days > 0 else 0)
        buckets[bucket] += Decimal(str(row["open"]))
    return [AgingRow(bucket=k, amount=v) for k, v in buckets.items()]


@router.get("/tax-summary", response_model=list[TaxSummaryRow])
async def tax_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[TaxSummaryRow]:
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT t.code AS tax_code, t.name AS tax_name, t.rate_pct AS rate_pct,
                       COALESCE(SUM(i.line_subtotal), 0) AS net,
                       COALESCE(SUM(i.line_tax), 0) AS tax
                FROM invoicing.invoice_lines i
                JOIN invoicing.invoices inv ON inv.id = i.invoice_id
                LEFT JOIN catalog.taxes t ON t.id = i.tax_id
                WHERE inv.org_id = :org AND inv.status IN ('posted','partial','paid')
                  AND (:from::date IS NULL OR inv.invoice_date >= :from)
                  AND (:to::date IS NULL OR inv.invoice_date <= :to)
                GROUP BY t.code, t.name, t.rate_pct
                ORDER BY net DESC
                """
            ),
            {"org": str(org.id), "from": date_from, "to": date_to},
        )
    ).mappings()
    return [
        TaxSummaryRow(
            tax_code=r["tax_code"],
            tax_name=r["tax_name"] or "No tax",
            rate_pct=Decimal(str(r["rate_pct"])) if r["rate_pct"] is not None else None,
            net=Decimal(str(r["net"])),
            tax=Decimal(str(r["tax"])),
        )
        for r in rows
    ]
