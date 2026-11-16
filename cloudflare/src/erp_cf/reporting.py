"""Exact, bounded cross-module report aggregates validated at one commit epoch.

No financial arithmetic crosses D1 as a JavaScript Number. Integer totals stay
unbounded in Python; converted tax groups use exact rational cents and round
once, half-even, after aggregation. Open balances use posted clearing effects.
"""

from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from app.modules.reporting import schemas

from erp_cf.accounting_reads import cents
from erp_cf.consistent_reads import ConsistentReader
from erp_cf.db import Statement
from erp_cf.documents import today
from erp_cf.money import from_coefficient

POSTED = "i.org_id=? AND i.status IN ('posted','partial','paid')"


def period(first=None, last=None):
    clauses, params = [], []
    for name, value, operator in [("invoice_date", first, ">="), ("invoice_date", last, "<=")]:
        if value:
            clauses.append(f"i.{name}{operator}?")
            params.append(value.isoformat())
    return "".join(" AND " + clause for clause in clauses), params


def scaled(value, scale):
    sign, value = ("-" if value < 0 else ""), abs(value)
    unit = 10**scale
    return Decimal(f"{sign}{value // unit}.{value % unit:0{scale}d}")


def bucket(days):
    return "current" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"


def converted(rates):
    total = sum((Fraction(amount * 10**8, fx) for fx, amount in rates.items()), Fraction())
    return cents(round(total))


def dashboard_dates(day):
    # Twelve complete weeks plus the current week, ending at this Monday.
    return day.replace(day=1), day - timedelta(days=day.weekday() + 84)


class Reports(ConsistentReader):
    async def read(self, name, **params):
        if name == "dashboard":
            day = today()
            month, start = dashboard_dates(day)
            return await self.run(
                lambda pages: self.dashboard(day, pages),
                initial=(
                    self.page(**self.invoice_query(date_from=min(month, start))),
                    self.page(**self.open_invoice_query()),
                    self.low_stock_query(),
                ),
            )
        return await self.run(lambda: getattr(self, name)(**params))

    def invoice_query(self, *, side=None, date_from=None, date_to=None):
        where, params = period(date_from, date_to)
        if side:
            where += " AND i.invoice_type IN (?,?)"
            params += [side, side + "_credit"]
        return {
            "projection": "i.id,i.invoice_type,i.invoice_date,i.party_id,i.party_name,"
            "CAST(i.total_base AS TEXT) AS total_base",
            "source": "invoicing_invoices i",
            "where": POSTED + where,
            "params": (self.org_id, *params),
            "cursors": ("i.rowid",),
        }

    async def invoice_rows(self, *, initial_rows=None, **params):
        async for row in self.rows(**self.invoice_query(**params), initial_rows=initial_rows):
            yield row

    def open_invoice_query(self, side=None):
        where, params = "i.org_id=? AND i.status IN ('posted','partial')", [self.org_id]
        if side:
            where += " AND i.invoice_type=?"
            params.append(side)
        source = (
            "invoicing_invoices i LEFT JOIN cf_settlement_effects e "
            "ON e.org_id=i.org_id AND e.invoice_id=i.id AND ("
            "(e.source_kind='payment' AND EXISTS(SELECT 1 FROM invoicing_payments p "
            "WHERE p.id=e.source_id AND p.status='recorded')) OR "
            "(e.source_kind='credit' AND EXISTS(SELECT 1 FROM invoicing_invoices c "
            "WHERE c.id=e.source_id AND c.status IN ('posted','partial','paid'))))"
        )
        return {
            "projection": "i.id,i.invoice_type,i.due_date,CAST(i.total_base AS TEXT) AS total_base,"
            "CAST(COALESCE(e.amount_base,0) AS TEXT) AS cleared_base",
            "source": source,
            "where": where,
            "params": tuple(params),
            "cursors": ("i.rowid", "COALESCE(e.rowid,0)"),
        }

    async def open_invoices(self, side=None, *, initial_rows=None):
        current = None
        async for row in self.rows(**self.open_invoice_query(side), initial_rows=initial_rows):
            if current is None or row["id"] != current["id"]:
                if current:
                    yield current
                current = {**row, "open_base": int(row["total_base"])}
            current["open_base"] -= int(row["cleared_base"])
        if current:
            yield current

    async def dashboard(self, day, pages):
        month, start = dashboard_dates(day)
        totals, opened, weeks = {"ar": 0, "ap": 0}, {"ar": 0, "ap": 0}, {}
        async for row in self.invoice_rows(date_from=min(month, start), initial_rows=pages[0]):
            side = row["invoice_type"][:2]
            amount = int(row["total_base"]) * (-1 if row["invoice_type"].endswith("_credit") else 1)
            invoice_date = date.fromisoformat(row["invoice_date"])
            if invoice_date >= month:
                totals[side] += amount
            week = (invoice_date - timedelta(days=invoice_date.weekday())).isoformat()
            point = weeks.setdefault(week, {"ar": 0, "ap": 0})
            point[side] += amount
        async for row in self.open_invoices(initial_rows=pages[1]):
            opened[row["invoice_type"][:2]] += row["open_base"] * (
                -1 if row["invoice_type"].endswith("_credit") else 1
            )
        low = pages[2][0]
        points = []
        for index in range(13):
            week = (start + timedelta(days=index * 7)).isoformat()
            point = weeks.get(week, {"ar": 0, "ap": 0})
            points.append(
                schemas.WeekPoint(
                    week_start=week, sales=cents(point["ar"]), purchases=cents(point["ap"])
                )
            )
        return schemas.DashboardOut(
            sales_mtd=cents(totals["ar"]),
            purchases_mtd=cents(totals["ap"]),
            open_ar=cents(opened["ar"]),
            open_ap=cents(opened["ap"]),
            low_stock_count=int(low["products"]) * int(low["warehouses"]) - int(low["above_min"]),
            weeks=points,
        )

    def low_stock_query(self):
        return Statement(
            "SELECT CAST((SELECT COUNT(*) FROM catalog_products p WHERE p.org_id=? "
            "AND p.status='active' AND p.type='goods' AND p.track_inventory=1 "
            "AND p.min_stock>0) AS TEXT) AS products,"
            "CAST((SELECT COUNT(*) FROM inventory_warehouses WHERE org_id=?) "
            "AS TEXT) AS warehouses,"
            "CAST((SELECT COUNT(*) FROM inventory_stock s JOIN catalog_products p "
            "ON p.id=s.product_id AND p.org_id=s.org_id JOIN inventory_warehouses w "
            "ON w.id=s.warehouse_id AND w.org_id=s.org_id WHERE s.org_id=? "
            "AND p.status='active' AND p.type='goods' AND p.track_inventory=1 "
            "AND p.min_stock>0 AND s.qty_on_hand>p.min_stock) AS TEXT) AS above_min",
            (self.org_id, self.org_id, self.org_id),
        )

    async def by_party(self, side, date_from=None, date_to=None):
        groups = {}
        async for row in self.invoice_rows(side=side, date_from=date_from, date_to=date_to):
            group = groups.setdefault(
                row["party_id"], {"total": 0, "invoice_count": 0, "name": None}
            )
            group["total"] += int(row["total_base"]) * (
                -1 if row["invoice_type"].endswith("_credit") else 1
            )
            group["invoice_count"] += row["invoice_type"] == side
            if row["party_name"] is not None:
                group["name"] = (
                    max(group["name"], row["party_name"])
                    if group["name"] is not None
                    else row["party_name"]
                )
        ordered = sorted(
            groups.items(),
            key=lambda item: (-item[1]["total"], item[1]["name"] or "", item[0] or ""),
        )
        return [
            schemas.ByCustomerRow(
                party_id=key,
                party_name=value["name"] or "Unknown",
                invoice_count=value["invoice_count"],
                total=cents(value["total"]),
            )
            for key, value in ordered
        ]

    async def aging(self, side):
        totals = {name: 0 for name in ["current", "31-60", "61-90", "90+"]}
        day = today()
        async for row in self.open_invoices(side):
            due = date.fromisoformat(row["due_date"]) if row["due_date"] else day
            totals[bucket(max((day - due).days, 0))] += row["open_base"]
        return [
            schemas.AgingRow(bucket=name, amount=cents(amount)) for name, amount in totals.items()
        ]

    async def stock_valuation(self):
        groups = {}
        async for row in self.rows(
            "w.id,w.code,w.name,CAST(s.qty_on_hand AS TEXT) AS qty,"
            "CAST(s.avg_cost AS TEXT) AS cost",
            "inventory_stock s JOIN inventory_warehouses w "
            "ON w.id=s.warehouse_id AND w.org_id=s.org_id",
            "s.org_id=? AND s.qty_on_hand!=0",
            (self.org_id,),
            cursors=("s.rowid",),
        ):
            group = groups.setdefault(
                row["id"],
                {"code": row["code"], "name": row["name"], "qty": 0, "value": 0, "products": 0},
            )
            group["products"] += 1
            group["qty"] += int(row["qty"])
            group["value"] += int(row["qty"]) * int(row["cost"])
        return [
            schemas.StockValuationRow(
                warehouse_code=row["code"],
                warehouse_name=row["name"],
                products=row["products"],
                qty_on_hand=scaled(row["qty"], 4),
                value=cents(round(Fraction(row["value"], 10**8))),
            )
            for row in sorted(groups.values(), key=lambda row: row["code"])
        ]

    async def tax_summary(self, date_from=None, date_to=None):
        where, params = period(date_from, date_to)
        groups = {}
        async for row in self.rows(
            "i.invoice_type,l.tax_id,t.code,t.name,CAST(l.tax_rate_pct AS TEXT) AS rate,"
            "CAST(l.line_subtotal AS TEXT) AS net,CAST(l.line_tax AS TEXT) AS tax,"
            "CAST(i.fx_rate AS TEXT) AS fx",
            "invoicing_invoice_lines l JOIN invoicing_invoices i ON i.id=l.invoice_id "
            "LEFT JOIN catalog_taxes t ON t.id=l.tax_id AND t.org_id=i.org_id",
            POSTED + where,
            (self.org_id, *params),
            cursors=("l.rowid",),
        ):
            direction = "output" if row["invoice_type"].startswith("ar") else "input"
            key = (direction, row["tax_id"], row["rate"])
            group = groups.setdefault(
                key, {"code": row["code"], "name": row["name"], "net": {}, "tax": {}}
            )
            sign, fx = (-1 if row["invoice_type"].endswith("_credit") else 1), int(row["fx"])
            for field in ["net", "tax"]:
                group[field][fx] = group[field].get(fx, 0) + sign * int(row[field])
        result = []
        for (direction, _, rate), group in groups.items():
            result.append(
                schemas.TaxSummaryRow(
                    direction=direction,
                    tax_code=group["code"],
                    tax_name=group["name"] or "No tax",
                    rate_pct=from_coefficient(rate, 2),
                    net=converted(group["net"]),
                    tax=converted(group["tax"]),
                )
            )
        return sorted(
            result,
            key=lambda row: (row.direction != "output", -row.net, row.tax_code or "", row.rate_pct),
        )
