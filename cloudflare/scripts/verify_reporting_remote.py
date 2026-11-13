"""Exact reports through the isolated Python Worker and real D1.

Creates synthetic records, verifies complete report versions and reverses all
posted financial effects. Test master data/history remain in isolated staging.
No credentials, tokens or outgoing emails are logged/sent.
"""

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main():
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run, day = uuid.uuid4().hex[:10], datetime.now(UTC).date().isoformat()
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        login = await client.post(
            "/api/auth/login",
            json={
                "email": credentials["email"],
                "password": credentials["password"],
            },
        )
        assert login.status_code == 200, f"Login returned {login.status_code}"
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}

        async def send(method, path, body=None, *, headers=None):
            headers = dict(headers or auth)
            if method != "GET":
                headers["Idempotency-Key"] = uuid.uuid4().hex
            for attempt in range(3):
                try:
                    return await client.request(method, path, headers=headers, json=body)
                except httpx.TransportError:
                    if attempt == 2:
                        raise

        async def call(method, path, body=None, *, expected=200, headers=None):
            response = await send(method, path, body, headers=headers)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}: {response.text[:400]}"
            )
            if method == "GET":
                assert response.headers["cache-control"] == "no-store"
            return response.json() if response.content else None

        async def create(path, body):
            return await call("POST", path, body, expected=201)

        async def report(name, query=""):
            return await call("GET", "/api/reports/" + name + query)

        baseline = await report("dashboard")
        baseline_aging = {row["bucket"]: Decimal(row["amount"]) for row in await report("aging")}
        party = await create(
            "/api/crm/contacts",
            {"name": f"Reports {run}", "is_customer": True, "is_supplier": True},
        )
        tax = await create(
            "/api/catalog/taxes", {"code": f"R{run}", "name": "Report tax", "rate_pct": "10"}
        )
        rounded_tax = await create(
            "/api/catalog/taxes", {"code": f"Z{run}", "name": "Report rounding", "rate_pct": "0"}
        )
        response = await send(
            "POST", "/api/currencies", {"code": "XTS", "name": "Testing currency"}
        )
        assert response.status_code in {201, 409}, response.text
        await call(
            "PUT", "/api/currencies/rates", {"currency": "XTS", "rate_date": day, "rate": "2"}
        )

        async def invoice(amount="100", *, posted=True, **fields):
            row = await create(
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": party["id"],
                    "currency": "XTS",
                    "due_date": day,
                    "lines": [
                        {
                            "description": "Report proof",
                            "qty": "1",
                            "unit_price": amount,
                            "tax_id": tax["id"],
                        }
                    ],
                    **fields,
                },
            )
            if posted:
                return await call("POST", f"/api/invoicing/invoices/{row['id']}/post")
            return row

        async def void(row):
            return await call("POST", f"/api/invoicing/invoices/{row['id']}/void")

        async def party_total(name, query=""):
            rows = await report(name, query)
            return next(row for row in rows if row["party_id"] == party["id"])

        ar, ap = await invoice(), await invoice("80", invoice_type="ap")
        credit = await invoice("20", invoice_type="ar_credit", original_invoice_id=ar["id"])
        draft = await invoice("99", posted=False)
        voided = await invoice("99")
        await void(voided)
        old = await invoice(
            currency="USD",
            invoice_date="2020-01-01",
            due_date="2020-01-01",
            lines=[{"description": "Old report proof", "qty": "1", "unit_price": "50"}],
        )
        pay = await create(
            "/api/invoicing/payments",
            {
                "party_id": party["id"],
                "direction": "in",
                "amount": "22",
                "method": "bank",
                "allocations": [{"invoice_id": ar["id"], "amount": "22"}],
            },
        )
        await call(
            "PATCH",
            f"/api/catalog/taxes/{tax['id']}",
            {"code": tax["code"], "name": tax["name"], "rate_pct": "20"},
        )
        newer = await invoice("30")
        dashboard = await report("dashboard")
        for field, change in {
            "sales_mtd": "62",
            "purchases_mtd": "44",
            "open_ar": "101",
            "open_ap": "44",
        }.items():
            assert Decimal(dashboard[field]) - Decimal(baseline[field]) == Decimal(change)
        assert len(dashboard["weeks"]) == 13
        current_monday = datetime.now(UTC).date()
        assert dashboard["weeks"][-1]["week_start"] <= current_monday.isoformat()
        sales = await party_total("sales-by-customer")
        assert sales["total"] == "112.00" and sales["invoice_count"] == 3
        dated = await party_total("sales-by-customer", f"?date_from={day}&date_to={day}")
        assert dated["total"] == "62.00" and dated["invoice_count"] == 2
        assert (await party_total("purchases-by-supplier"))["total"] == "44.00"
        aging = {row["bucket"]: Decimal(row["amount"]) for row in await report("aging")}
        assert aging["current"] - baseline_aging["current"] == Decimal("51")
        assert aging["90+"] - baseline_aging["90+"] == Decimal("50")
        tax_rows = [row for row in await report("tax-summary") if row["tax_code"] == tax["code"]]
        assert sorted(
            (row["direction"], row["rate_pct"], row["net"], row["tax"]) for row in tax_rows
        ) == [
            ("input", "10.00", "40.00", "4.00"),
            ("output", "10.00", "40.00", "4.00"),
            ("output", "20.00", "15.00", "3.00"),
        ]
        print(
            "PASS: dashboard, credits, dated party totals, residual aging and tax snapshots",
            flush=True,
        )

        warehouse = await create(
            "/api/inventory/warehouses", {"code": f"R{run}", "name": "Report warehouse"}
        )
        product = await create(
            "/api/catalog/products", {"name": f"Report stock {run}", "min_stock": "5"}
        )
        receipt = await create(
            "/api/inventory/receipts",
            {
                "warehouse_id": warehouse["id"],
                "lines": [{"product_id": product["id"], "qty": "2", "unit_cost": "10"}],
            },
        )
        await call("POST", f"/api/inventory/receipts/{receipt['id']}/post")
        valuation = next(
            row
            for row in await report("stock-valuation")
            if row["warehouse_code"] == warehouse["code"]
        )
        assert (
            valuation["products"] == 1
            and valuation["qty_on_hand"] == "2.0000"
            and valuation["value"] == "20.00"
        )
        low_rows = await call("GET", "/api/inventory/stock?low_only=true")
        assert (await report("dashboard"))["low_stock_count"] == len(low_rows)
        rounded = await invoice(
            lines=[
                {
                    "description": "Half a base cent",
                    "qty": "1",
                    "unit_price": "0.01",
                    "tax_id": rounded_tax["id"],
                }
                for _ in range(2)
            ]
        )
        rounded_row = next(
            row for row in await report("tax-summary") if row["tax_code"] == rounded_tax["code"]
        )
        assert rounded_row["net"] == "0.01" and rounded_row["tax"] == "0.00"
        await void(rounded)
        print(
            "PASS: exact warehouse valuation, never-stocked low counts "
            "and aggregate half-even rounding",
            flush=True,
        )

        maximum = []
        for _ in range(10):
            maximum.append(
                await invoice(
                    currency="USD",
                    lines=[
                        {
                            "description": "Maximum coefficient",
                            "qty": "10000",
                            "unit_price": "999999999999.999999",
                        }
                    ],
                )
            )
        sales = await party_total("sales-by-customer")
        assert sales["total"] == "100000000000000111.90"
        assert sales["invoice_count"] == 13
        for row in maximum:
            await void(row)
        assert (await party_total("sales-by-customer"))["total"] == "112.00"
        print(
            "PASS: real D1 report total exceeds signed int64 without rounding or SUM overflow",
            flush=True,
        )

        # More than 500 lines forces native report pagination. The race may
        # return either whole committed version or an explicit busy response.
        paged = await invoice(
            currency="USD",
            lines=[
                {
                    "description": f"Paged line {index}",
                    "qty": "1",
                    "unit_price": "0.01",
                    "tax_id": rounded_tax["id"],
                }
                for index in range(601)
            ],
        )
        before = next(
            row for row in await report("tax-summary") if row["tax_code"] == rounded_tax["code"]
        )
        assert before["net"] == "6.01"

        async def concurrent_read(delay):
            await asyncio.sleep(delay)
            result = await send("GET", "/api/reports/tax-summary")
            if result.status_code == 503:
                assert result.json()["error"]["code"] == "report_busy"
                return "busy"
            assert result.status_code == 200, result.text[:400]
            row = next(
                (row for row in result.json() if row["tax_code"] == rounded_tax["code"]), None
            )
            amount = row["net"] if row else "0.00"
            assert amount in {"0.00", "6.01"}, f"Mixed report version: {amount}"
            return amount

        async def concurrent_void():
            await asyncio.sleep(0.02)
            await void(paged)

        outcomes = await asyncio.gather(
            *[concurrent_read(delay) for delay in [0, 0.005, 0.01, 0.05, 0.1, 0.2]],
            concurrent_void(),
        )
        print(
            "PASS: 601-line paged report/void race returns complete versions:",
            sorted(set(outcomes[:-1])),
            flush=True,
        )

        role = await create(
            "/api/roles",
            {
                "code": f"report-denied-{run}",
                "name": "No reports",
                "permission_codes": ["hr.leave.request"],
            },
        )
        email, password = f"report-denied-{run}@example.com", secrets.token_urlsafe(24)
        user = await create(
            "/api/users",
            {
                "email": email,
                "password": password,
                "full_name": "Report permission proof",
                "role_codes": [role["code"]],
            },
        )
        denied_login = await client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert denied_login.status_code == 200
        denied = {"Authorization": "Bearer " + denied_login.json()["access_token"]}
        for name in [
            "dashboard",
            "sales-by-customer",
            "purchases-by-supplier",
            "aging",
            "stock-valuation",
            "tax-summary",
        ]:
            await call("GET", "/api/reports/" + name, expected=403, headers=denied)
        await call("PATCH", f"/api/users/{user['id']}", {"is_active": False})
        print(
            "PASS: every report rejects accounts without reports.view; test session revoked",
            flush=True,
        )

        await call("POST", f"/api/invoicing/payments/{pay['id']}/void")
        for row in [credit, ar, ap, old, newer]:
            await void(row)
        await call("DELETE", f"/api/invoicing/invoices/{draft['id']}", expected=204)
        await call("POST", f"/api/inventory/receipts/{receipt['id']}/void")
        current = await report("dashboard")
        for field in ["sales_mtd", "purchases_mtd", "open_ar", "open_ap"]:
            assert current[field] == baseline[field], (field, current[field], baseline[field])
        assert not any(row["party_id"] == party["id"] for row in await report("sales-by-customer"))
        assert not any(
            row["warehouse_code"] == warehouse["code"] for row in await report("stock-valuation")
        )
        print(
            "PASS: exact reversals restore starting monetary reports and warehouse stock",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
