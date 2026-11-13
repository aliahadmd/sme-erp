"""Real staging order/quotation contracts; creates synthetic demo documents."""

import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main():
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=120) as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, f"Login returned {response.status_code}"
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def call(method, path, *, body=None, key=None, expected=200, params=None):
            headers = dict(auth)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            for attempt in range(3):
                try:
                    result = await client.request(
                        method, path, headers=headers, json=body, params=params
                    )
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            assert result.status_code == expected, (
                f"{method} {path}: {result.status_code}: {result.text[:500]}"
            )
            return result.json()

        currencies = await call("GET", "/api/currencies")
        if not any(r["code"] == "XDO" for r in currencies):
            await call(
                "POST",
                "/api/currencies",
                body={"code": "XDO", "name": "Document proof currency"},
                expected=201,
            )
        for day, rate in [("2026-01-01", "2"), ("2026-03-01", "4")]:
            await call(
                "PUT",
                "/api/currencies/rates",
                body={"currency": "XDO", "rate_date": day, "rate": rate},
            )
        party = await call(
            "POST",
            "/api/crm/contacts",
            body={
                "name": f"Straße 客户 {run}",
                "is_customer": True,
                "is_supplier": True,
                "currency": "XDO",
            },
            expected=201,
        )
        uom = await call(
            "POST",
            "/api/catalog/uoms",
            body={"code": f"D{run}", "name": "Document proof unit"},
            expected=201,
        )
        tax = await call(
            "POST",
            "/api/catalog/taxes",
            body={"code": f"D{run}", "name": "Document proof VAT", "rate_pct": "18"},
            expected=201,
        )
        product = await call(
            "POST",
            "/api/catalog/products",
            body={
                "name": f"Document Widget {run}",
                "sale_price": "10",
                "cost_price": "6",
                "uom_id": uom["id"],
                "sale_tax_id": tax["id"],
                "purchase_tax_id": tax["id"],
            },
            expected=201,
        )
        lines = [{"product_id": product["id"], "qty": "2.5", "discount_pct": "12.5"}]
        order_body = {"customer_id": party["id"], "order_date": "2026-02-01", "lines": lines}
        results = await asyncio.gather(
            *[
                call("POST", "/api/sales/orders", body=order_body, key=f"order-{run}", expected=201)
                for _ in range(5)
            ]
        )
        assert len({r["id"] for r in results}) == 1
        order = results[0]
        assert order["currency"] == "XDO" and order["total"] == "25.82"
        assert order["lines"][0]["tax_rate_pct"] == "18.00"
        assert order["lines"][0]["uom_code"] == uom["code"]
        found = await call(
            "GET", "/api/sales/orders", params={"q": "STRASSE 客户", "party_id": party["id"]}
        )
        assert found["total"] == 1 and found["items"][0]["id"] == order["id"]
        lines.append({"description": "Service", "qty": "1", "unit_price": "100"})
        changed = await call(
            "PATCH",
            f"/api/sales/orders/{order['id']}",
            body={"lines": lines, "notes": "Proof draft edit"},
        )
        assert changed["total"] == "125.82" and len(changed["lines"]) == 2
        confirmed = await call(
            "POST", f"/api/sales/orders/{order['id']}/confirm", key=f"confirm-{run}"
        )
        assert confirmed["status"] == "confirmed" and confirmed["confirmed_by"]
        replay = await call(
            "POST", f"/api/sales/orders/{order['id']}/confirm", key=f"confirm-{run}"
        )
        assert replay == confirmed
        await call(
            "PATCH", f"/api/sales/orders/{order['id']}", body={"notes": "Too late"}, expected=409
        )
        outstanding = await call("GET", f"/api/sales/orders/{order['id']}/outstanding")
        assert Decimal(outstanding["outstanding"][product["id"]]) == Decimal("2.5")
        cancelled = await call("POST", f"/api/sales/orders/{order['id']}/cancel")
        assert cancelled["status"] == "cancelled"
        await call("POST", f"/api/sales/orders/{order['id']}/confirm", expected=409)
        print(
            "PASS: sales totals, Unicode search, atomic replay, draft editing and lifecycle guards"
        )

        purchase = await call(
            "POST",
            "/api/purchasing/orders",
            body={
                "supplier_id": party["id"],
                "order_date": "2026-02-01",
                "lines": [{"product_id": product["id"], "qty": "10"}],
            },
            expected=201,
        )
        assert purchase["lines"][0]["unit_price"] == "6.000000" and purchase["total"] == "70.80"
        purchase = await call("POST", f"/api/purchasing/orders/{purchase['id']}/confirm")
        assert purchase["status"] == "confirmed"
        print("PASS: purchase cost/tax defaults and independent order numbering")

        quotation = await call(
            "POST",
            "/api/sales/quotations",
            body={
                "customer_id": party["id"],
                "quote_date": "2026-02-01",
                "valid_until": "2099-12-31",
                "lines": lines,
            },
            expected=201,
        )
        assert quotation["total"] == "125.82"
        for action, status in [("send", "sent"), ("accept", "accepted")]:
            result = await call("POST", f"/api/sales/quotations/{quotation['id']}/{action}")
            assert result["status"] == status
        await call(
            "PATCH",
            f"/api/sales/quotations/{quotation['id']}",
            body={"notes": "Too late"},
            expected=409,
        )
        await call(
            "PATCH",
            f"/api/catalog/products/{product['id']}",
            body={"name": f"Renamed {run}", "sale_price": "99"},
        )
        keys = [f"convert-{run}-{i}" for i in range(6)]

        async def conversion(key):
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"/api/sales/quotations/{quotation['id']}/convert",
                        headers={**auth, "Idempotency-Key": key},
                    )
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            assert response.status_code in {200, 409}, f"Conversion returned {response.status_code}"
            return key, response.status_code, response.json()

        converted = await asyncio.gather(*[conversion(key) for key in keys])
        assert sorted(status for _, status, _ in converted) == [200, 409, 409, 409, 409, 409]
        key, _, result = next(r for r in converted if r[1] == 200)
        repeated = await call("POST", f"/api/sales/quotations/{quotation['id']}/convert", key=key)
        assert repeated == result
        copied = await call("GET", f"/api/sales/orders/{result['order_id']}")
        assert copied["total"] == quotation["total"] and copied["status"] == "draft"
        assert copied["lines"][0]["product_name"] == product["name"]
        assert copied["lines"][0]["unit_price"] == "10.000000"
        saved_quote = await call("GET", f"/api/sales/quotations/{quotation['id']}")
        assert (
            saved_quote["converted_order_id"] == copied["id"]
            and saved_quote["status"] == "converted"
        )
        print("PASS: conversion race commits one order and preserves agreed catalog snapshots")

        exact = await call(
            "POST",
            "/api/sales/orders",
            body={
                "customer_id": party["id"],
                "currency": "USD",
                "lines": [
                    {
                        "description": "Exact price",
                        "qty": "0.0001",
                        "unit_price": "999999999999.999999",
                    }
                ],
            },
            expected=201,
        )
        stored = await call("GET", f"/api/sales/orders/{exact['id']}")
        assert (
            stored["lines"][0]["unit_price"] == "999999999999.999999"
            and stored["total"] == "100000000.00"
        )
        await call(
            "POST",
            "/api/sales/orders",
            body={"customer_id": party["id"], "lines": [{"qty": "0.00001", "unit_price": "1"}]},
            expected=422,
        )
        await client.post("/api/auth/logout")
        print("PASS: exact 18-digit price transport and rounded-zero quantity rejection")


if __name__ == "__main__":
    asyncio.run(main())
