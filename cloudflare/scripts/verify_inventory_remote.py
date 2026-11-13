"""Synthetic stock/order/bookkeeping proof through the isolated staging Worker.

--failure-probe requires fixtures/inventory_fault_probe.sql. Remove the fixture
trigger after verification. Never install it in production. No credentials or
tokens are printed; demo documents stay in isolated staging.
"""

import argparse
import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main(failure_probe=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, f"Login returned {response.status_code}"
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def send(method, path, *, body=None, key=None, params=None):
            headers = dict(auth)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            for attempt in range(3):
                try:
                    return await client.request(
                        method, path, headers=headers, json=body, params=params
                    )
                except httpx.TransportError:
                    if attempt == 2:
                        raise

        async def call(method, path, *, body=None, key=None, expected=200, params=None):
            result = await send(method, path, body=body, key=key, params=params)
            assert result.status_code == expected, (
                f"{method} {path}: {result.status_code}: {result.text[:500]}"
            )
            return result.json()

        async def create(path, body):
            return await call("POST", path, body=body, expected=201)

        warehouse = await create(
            "/api/inventory/warehouses", {"code": f"I{run}", "name": f"Inventory proof {run}"}
        )
        product = await create(
            "/api/catalog/products",
            {
                "name": f"Stock proof {run}",
                "sale_price": "20",
                "cost_price": "10",
                "min_stock": "5",
            },
        )
        other = await create(
            "/api/catalog/products",
            {
                "name": f"Other stock {run}",
                "sale_price": "200",
                "cost_price": "100",
                "min_stock": "5",
            },
        )
        party = await create(
            "/api/crm/contacts",
            {"name": f"Stock party {run}", "is_customer": True, "is_supplier": True},
        )

        async def stock(product_id, low=False):
            rows = await call(
                "GET",
                "/api/inventory/stock",
                params={"warehouse_id": warehouse["id"], "low_only": low},
            )
            return next((r for r in rows if r["product_id"] == product_id), None)

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(r["total_debit"]) for r in rows) == sum(
                Decimal(r["total_credit"]) for r in rows
            )
            return {r["code"]: Decimal(r["balance"]) for r in rows}

        baseline = await books()
        assert (await stock(product["id"], low=True))["qty_on_hand"] == "0.0000"

        async def order(qty, price, purchase=False):
            path = "/api/purchasing/orders" if purchase else "/api/sales/orders"
            row = await create(
                path,
                {
                    "supplier_id" if purchase else "customer_id": party["id"],
                    "lines": [{"product_id": product["id"], "qty": qty, "unit_price": price}],
                },
            )
            await call("POST", f"{path}/{row['id']}/confirm")
            return row

        async def document(kind, data):
            return await create(f"/api/inventory/{kind}", {"warehouse_id": warehouse["id"], **data})

        async def action(kind, row, name="post", *, key=None, expected=200):
            return await call(
                "POST", f"/api/inventory/{kind}/{row['id']}/{name}", key=key, expected=expected
            )

        async def receive(qty, cost, *, product_id=None):
            row = await document(
                "receipts",
                {
                    "lines": [
                        {"product_id": product_id or product["id"], "qty": qty, "unit_cost": cost}
                    ]
                },
            )
            await action("receipts", row)
            return row

        if failure_probe:
            po = await order("1", "1", purchase=True)
            draft = await document(
                "receipts",
                {"source_po_id": po["id"], "notes": "Cloudflare inventory atomic rollback probe"},
            )
            key = f"stock-failure-{run}"
            await action("receipts", draft, key=key, expected=409)
            viewed = await call("GET", f"/api/inventory/receipts/{draft['id']}")
            assert viewed["status"] == "draft" and viewed["posted_at"] is None
            viewed_po = await call("GET", f"/api/purchasing/orders/{po['id']}")
            assert (
                viewed_po["status"] == "confirmed"
                and Decimal(viewed_po["lines"][0]["qty_received"]) == 0
            )
            assert await stock(product["id"]) is None
            assert (await books())["1200"] == baseline["1200"]
            retry = await document("receipts", {"source_po_id": po["id"]})
            await action("receipts", retry, key=key)
            await action("receipts", retry, "void")
            assert (await books())["1200"] == baseline["1200"]
            print("PASS: real D1 journal fault rolls back stock, progress, books and receipt claim")

        po1 = await order("10", "10", purchase=True)
        r1 = await document("receipts", {"source_po_id": po1["id"]})
        results = await asyncio.gather(
            *[action("receipts", r1, key=f"receipt-{run}") for _ in range(6)]
        )
        assert all(r["status"] == "posted" for r in results)
        assert (await stock(product["id"]))["qty_on_hand"] == "10.0000"
        po2 = await order("10", "14", purchase=True)
        r2 = await document("receipts", {"source_po_id": po2["id"]})
        await action("receipts", r2)
        current = await stock(product["id"])
        assert current["qty_on_hand"] == "20.0000" and current["avg_cost"] == "12.000000"
        so = await order("5", "20")
        delivery = await document("deliveries", {"source_so_id": so["id"]})
        await action("deliveries", delivery)
        current = await stock(product["id"])
        current_books = await books()
        assert current["stock_value"] == "180.00"
        assert current_books["1200"] - baseline["1200"] == Decimal("180")
        assert current_books["5000"] - baseline["5000"] == Decimal("60")
        moves = await call(
            "GET", "/api/inventory/moves", params={"product_id": product["id"], "limit": 50}
        )
        assert next(r for r in moves["items"] if r["move_type"] == "delivery")["cogs"] == "60.00"
        later = await receive("3", "20")
        await action("deliveries", delivery, "void")
        assert (await stock(product["id"]))["stock_value"] == "300.00"
        current_books = await books()
        assert current_books["1200"] - baseline["1200"] == Decimal("300")
        assert current_books["5000"] == baseline["5000"]
        await action("receipts", later, "void")
        await action("receipts", r2, "void")
        await action("receipts", r1, "void")
        assert await stock(product["id"]) is None
        assert (await books())["1200"] == baseline["1200"]
        viewed_so = await call("GET", f"/api/sales/orders/{so['id']}")
        assert (
            viewed_so["status"] == "confirmed"
            and Decimal(viewed_so["lines"][0]["qty_delivered"]) == 0
        )
        print(
            "PASS: moving average, exact stock/ledger values, replay "
            "and interleaved cost/progress reversals"
        )

        supply = await receive("5", "10")
        drafts = []
        for _ in range(2):
            so = await order("3", "20")
            drafts.append(await document("deliveries", {"source_so_id": so["id"]}))
        results = await asyncio.gather(
            *[
                send("POST", f"/api/inventory/deliveries/{row['id']}/post", key=f"race-{run}-{i}")
                for i, row in enumerate(drafts)
            ]
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        winner = drafts[next(i for i, r in enumerate(results) if r.status_code == 200)]
        loser = drafts[next(i for i, r in enumerate(results) if r.status_code == 409)]
        assert (await stock(product["id"]))["qty_on_hand"] == "2.0000"
        assert (await call("GET", f"/api/inventory/deliveries/{loser['id']}"))["status"] == "draft"
        await action("deliveries", winner, "void")
        await action("receipts", supply, "void")
        assert (await books())["1200"] == baseline["1200"]
        print("PASS: insufficient-stock race has one complete winner and one unchanged draft")

        await receive("3", "100", product_id=other["id"])
        adjustment = await document(
            "adjustments",
            {
                "reason": "count",
                "lines": [
                    {"product_id": product["id"], "qty": "2", "unit_cost": "4"},
                    {"product_id": other["id"], "qty": "-1"},
                ],
            },
        )
        await action("adjustments", adjustment)
        current_books = await books()
        assert current_books["1200"] - baseline["1200"] == Decimal("208")
        assert current_books["5200"] - baseline["5200"] == Decimal("92")
        assert (await stock(product["id"], low=True))["is_low"]
        assert (await stock(other["id"]))["stock_value"] == "200.00"
        print(
            "PASS: mixed-sign adjustment books net value "
            "and low-stock includes zero/unstocked products"
        )

        exact_product = await create("/api/catalog/products", {"name": f"Exact stock {run}"})
        exact = await receive("0.0001", "999999999999.999999", product_id=exact_product["id"])
        current = await stock(exact_product["id"])
        assert (
            current["avg_cost"] == "999999999999.999999"
            and current["stock_value"] == "100000000.00"
        )
        await action("receipts", exact, "void")
        large = await document(
            "receipts",
            {"lines": [{"product_id": product["id"], "qty": "0.0001", "unit_cost": "4"}] * 360},
        )
        await action("receipts", large)
        assert (await stock(product["id"]))["qty_on_hand"] == "2.0360"
        await action("receipts", large, "void")
        assert (await stock(product["id"]))["qty_on_hand"] == "2.0000"
        assert (await books())["1200"] - baseline["1200"] == Decimal("208")
        await client.post("/api/auth/logout")
        print("PASS: exact 18-digit cost and 360-line post/void preserve snapshots and balances")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--failure-probe", action="store_true")
    asyncio.run(main(parser.parse_args().failure_probe))
