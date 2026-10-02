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
    direction: str  # output (sales, credit notes netted) | input (purchases)
    tax_code: str | None
    tax_name: str | None
    rate_pct: Decimal | None
    net: Decimal
    tax: Decimal


# All report amounts are BASE currency (stored *_base snapshots or document
# amounts divided by the document's snapshot rate). Credit notes are netted
# (negated) against their side; void/draft documents are excluded.
POSTED = "('posted','partial','paid')"
SIGNED_TOTAL_BASE = "CASE WHEN invoice_type LIKE '%\\_credit' THEN -total_base ELSE total_base END"
# Open (unsettled) amount in base currency: invoices owe what is not paid or
# credited; credit notes not yet consumed reduce what the party owes.
OPEN_BASE = (
    "CASE WHEN invoice_type LIKE '%\\_credit' "
    "THEN -(total - amount_paid) / fx_rate "
    "ELSE (total - amount_paid - applied_credits) / fx_rate END"
)


def _side(side: str) -> dict[str, str]:
    return {"t": side, "tc": f"{side}_credit"}


def _q(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


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

    async def _period_total(side: str) -> Decimal:
        return _q(
            await session.scalar(
                text(
                    f"SELECT COALESCE(SUM({SIGNED_TOTAL_BASE}), 0) FROM invoicing.invoices "
                    f"WHERE org_id = :org AND invoice_type IN (:t, :tc) "
                    f"AND status IN {POSTED} AND invoice_date >= :d"
                ),
                {"org": str(org.id), "d": month_start, **_side(side)},
            )
        )

    async def _open(side: str) -> Decimal:
        return _q(
            await session.scalar(
                text(
                    f"SELECT COALESCE(SUM({OPEN_BASE}), 0) FROM invoicing.invoices "
                    "WHERE org_id = :org AND invoice_type IN (:t, :tc) "
                    "AND status IN ('posted','partial')"
                ),
                {"org": str(org.id), **_side(side)},
            )
        )

    low_stock = (
        await session.scalar(
            text(
                """
                SELECT COUNT(*) FROM catalog.products p
                CROSS JOIN inventory.warehouses w
                LEFT JOIN inventory.stock s
                       ON s.product_id = p.id AND s.warehouse_id = w.id
                WHERE p.org_id = :org AND w.org_id = :org
                  AND p.status = 'active' AND p.type = 'goods' AND p.track_inventory
                  AND p.min_stock > 0
                  AND COALESCE(s.qty_on_hand, 0) <= p.min_stock
                """
            ),
            {"org": str(org.id)},
        )
    ) or 0

    # 12 full weeks + the current one, base currency, credit notes netted.
    today = date.today()
    start = today - timedelta(days=today.weekday() + 77)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT date_trunc('week', invoice_date)::date AS week_start,
                       LEFT(invoice_type, 2) AS side,
                       COALESCE(SUM({SIGNED_TOTAL_BASE}), 0) AS total
                FROM invoicing.invoices
                WHERE org_id = :org AND status IN {POSTED} AND invoice_date >= :start
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
        buckets[week][row["side"]] += Decimal(str(row["total"]))
    weeks = []
    for i in range(13):
        week = (start + timedelta(days=7 * i)).isoformat()
        point = buckets.get(week, {})
        weeks.append(
            WeekPoint(
                week_start=week,
                sales=point.get("ar", Decimal("0")),
                purchases=point.get("ap", Decimal("0")),
            )
        )

    return DashboardOut(
        sales_mtd=await _period_total("ar"),
        purchases_mtd=await _period_total("ap"),
        open_ar=await _open("ar"),
        open_ap=await _open("ap"),
        low_stock_count=int(low_stock),
        weeks=weeks,
    )


async def _by_party(
    session: AsyncSession,
    org_id: uuid.UUID,
    side: str,
    date_from: date | None,
    date_to: date | None,
) -> list[ByCustomerRow]:
    rows = (
        await session.execute(
            text(
                f"""
                SELECT party_id, COALESCE(MAX(party_name), 'Unknown') AS party_name,
                       COUNT(*) FILTER (WHERE invoice_type = :t) AS invoice_count,
                       COALESCE(SUM({SIGNED_TOTAL_BASE}), 0) AS total
                FROM invoicing.invoices
                WHERE org_id = :org AND invoice_type IN (:t, :tc)
                  AND status IN {POSTED}
                  AND (CAST(:from AS date) IS NULL OR invoice_date >= CAST(:from AS date))
                  AND (CAST(:to AS date) IS NULL OR invoice_date <= CAST(:to AS date))
                GROUP BY party_id ORDER BY total DESC
                """
            ),
            {"org": str(org_id), "from": date_from, "to": date_to, **_side(side)},
        )
    ).mappings()
    return [
        ByCustomerRow(
            party_id=r["party_id"],
            party_name=r["party_name"],
            invoice_count=r["invoice_count"],
            total=_q(r["total"]),
        )
        for r in rows
    ]


@router.get("/sales-by-customer", response_model=list[ByCustomerRow])
async def sales_by_customer(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[ByCustomerRow]:
    org = await get_organization(session)
    return await _by_party(session, org.id, "ar", date_from, date_to)


@router.get("/purchases-by-supplier", response_model=list[ByCustomerRow])
async def purchases_by_supplier(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[ByCustomerRow]:
    org = await get_organization(session)
    return await _by_party(session, org.id, "ap", date_from, date_to)


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
            value=_q(r["value"]),
        )
        for r in rows
    ]


def _bucket(days: int) -> str:
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
    """Open invoice balances (after payments and applied credits) in base
    currency, bucketed by days past due. Unconsumed credit notes and
    on-account payments are not aged."""
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                """
                SELECT due_date,
                       COALESCE(SUM((total - amount_paid - applied_credits) / fx_rate), 0) AS open
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
        buckets[_bucket(max((today - due).days, 0))] += Decimal(str(row["open"]))
    return [AgingRow(bucket=k, amount=_q(v)) for k, v in buckets.items()]


@router.get("/tax-summary", response_model=list[TaxSummaryRow])
async def tax_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[TaxSummaryRow]:
    """Output tax (sales, credit notes netted) and input tax (purchases)
    separately, grouped by the rate SNAPSHOT on each line, in base currency."""
    org = await get_organization(session)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT CASE WHEN inv.invoice_type LIKE 'ar%' THEN 'output' ELSE 'input' END
                           AS direction,
                       MAX(t.code) AS tax_code, MAX(t.name) AS tax_name,
                       i.tax_rate_pct AS rate_pct,
                       COALESCE(SUM(
                           CASE WHEN inv.invoice_type LIKE '%\\_credit' THEN -1 ELSE 1 END
                           * i.line_subtotal / inv.fx_rate), 0) AS net,
                       COALESCE(SUM(
                           CASE WHEN inv.invoice_type LIKE '%\\_credit' THEN -1 ELSE 1 END
                           * i.line_tax / inv.fx_rate), 0) AS tax
                FROM invoicing.invoice_lines i
                JOIN invoicing.invoices inv ON inv.id = i.invoice_id
                LEFT JOIN catalog.taxes t ON t.id = i.tax_id
                WHERE inv.org_id = :org AND inv.status IN {POSTED}
                  AND (CAST(:from AS date) IS NULL OR inv.invoice_date >= CAST(:from AS date))
                  AND (CAST(:to AS date) IS NULL OR inv.invoice_date <= CAST(:to AS date))
                GROUP BY 1, i.tax_id, i.tax_rate_pct
                ORDER BY 1 DESC, net DESC
                """
            ),
            {"org": str(org.id), "from": date_from, "to": date_to},
        )
    ).mappings()
    return [
        TaxSummaryRow(
            direction=r["direction"],
            tax_code=r["tax_code"],
            tax_name=r["tax_name"] or "No tax",
            rate_pct=Decimal(str(r["rate_pct"])) if r["rate_pct"] is not None else None,
            net=_q(r["net"]),
            tax=_q(r["tax"]),
        )
        for r in rows
    ]
