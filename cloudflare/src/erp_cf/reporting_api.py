"""Existing read-only reporting routes on the native D1 report reader."""

from datetime import date

from app.modules.reporting import schemas
from fastapi import APIRouter, Depends, Query, Request

from erp_cf.db import D1Database
from erp_cf.identity import environment, require
from erp_cf.reporting import Reports

router = APIRouter(prefix="/api/reports", dependencies=[Depends(require("reports.view"))])


def reader(request):
    env = environment(request)
    return Reports(D1Database(env.DB), str(env.ORG_ID))


@router.get("/dashboard", response_model=schemas.DashboardOut)
async def dashboard(request: Request):
    return await reader(request).read("dashboard")


@router.get("/sales-by-customer", response_model=list[schemas.ByCustomerRow])
async def sales_by_customer(
    request: Request, date_from: date | None = None, date_to: date | None = None
):
    return await reader(request).read("by_party", side="ar", date_from=date_from, date_to=date_to)


@router.get("/purchases-by-supplier", response_model=list[schemas.ByCustomerRow])
async def purchases_by_supplier(
    request: Request, date_from: date | None = None, date_to: date | None = None
):
    return await reader(request).read("by_party", side="ap", date_from=date_from, date_to=date_to)


@router.get("/stock-valuation", response_model=list[schemas.StockValuationRow])
async def stock_valuation(request: Request):
    return await reader(request).read("stock_valuation")


@router.get("/aging", response_model=list[schemas.AgingRow])
async def aging(request: Request, invoice_type: str = Query("ar", pattern=r"^(ar|ap)$")):
    return await reader(request).read("aging", side=invoice_type)


@router.get("/tax-summary", response_model=list[schemas.TaxSummaryRow])
async def tax_summary(request: Request, date_from: date | None = None, date_to: date | None = None):
    return await reader(request).read("tax_summary", date_from=date_from, date_to=date_to)
