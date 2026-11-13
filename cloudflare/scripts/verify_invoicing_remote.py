"""Real isolated Worker/D1 acceptance for invoices, payments, FX and reversals.

--failure-probe requires fixtures/invoicing_fault_probe.sql. Remove its trigger
after verification. No emails are sent. Credentials and tokens are never logged.
"""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
PROBE = "Cloudflare invoicing atomic rollback probe"


async def trade_cycle(call, create, party, books, baseline, run):
    """A complete buy/sell cycle with source progress, stock and books together."""
    warehouse = await create(
        "/api/inventory/warehouses", {"code": f"T{run}", "name": "Trade proof"}
    )
    product = await create(
        "/api/catalog/products",
        {"name": f"Trade proof {run}", "sale_price": "20", "cost_price": "10"},
    )

    async def order(purchase):
        path = "/api/purchasing/orders" if purchase else "/api/sales/orders"
        row = await create(
            path,
            {
                "supplier_id" if purchase else "customer_id": party["id"],
                "lines": [
                    {
                        "product_id": product["id"],
                        "qty": "3",
                        "unit_price": "10" if purchase else "20",
                    }
                ],
            },
        )
        await call("POST", f"{path}/{row['id']}/confirm")
        return path, row

    async def bill(order, purchase):
        row = await create(
            "/api/invoicing/invoices",
            {
                "invoice_type": "ap" if purchase else "ar",
                "party_id": party["id"],
                "source_order_id": order["id"],
            },
        )
        return await call("POST", f"/api/invoicing/invoices/{row['id']}/post")

    async def movement(order, purchase):
        path = "/api/inventory/receipts" if purchase else "/api/inventory/deliveries"
        row = await create(
            path,
            {
                "warehouse_id": warehouse["id"],
                "source_po_id" if purchase else "source_so_id": order["id"],
            },
        )
        await call("POST", f"{path}/{row['id']}/post")
        return path, row

    async def pay(invoice, purchase):
        return await create(
            "/api/invoicing/payments",
            {
                "party_id": party["id"],
                "direction": "out" if purchase else "in",
                "method": "bank",
                "amount": invoice["total"],
                "allocations": [{"invoice_id": invoice["id"], "amount": invoice["total"]}],
            },
        )

    po_path, po = await order(True)
    ap = await bill(po, True)
    assert (await call("GET", f"{po_path}/{po['id']}"))["status"] == "invoiced"
    receipt_path, receipt = await movement(po, True)
    supplier_pay = await pay(ap, True)
    so_path, so = await order(False)
    delivery_path, delivery = await movement(so, False)
    ar = await bill(so, False)
    customer_pay = await pay(ar, False)
    for path, row in [(po_path, po), (so_path, so)]:
        assert (await call("GET", f"{path}/{row['id']}"))["status"] == "closed"
    current = await books()
    assert current["1100"] == baseline["1100"] and current["2000"] == baseline["2000"]
    assert current["1200"] == baseline["1200"] and current["2050"] == baseline["2050"]
    assert current["5000"] - baseline["5000"] == Decimal("30")
    assert current["4000"] - baseline["4000"] == Decimal("-60")
    for invoice in [ar, ap]:
        await call("POST", f"/api/invoicing/invoices/{invoice['id']}/void", expected=409)
    for pay_row in [customer_pay, supplier_pay]:
        await call("POST", f"/api/invoicing/payments/{pay_row['id']}/void")
    for invoice in [ar, ap]:
        await call("POST", f"/api/invoicing/invoices/{invoice['id']}/void")
    for path, row in [(delivery_path, delivery), (receipt_path, receipt)]:
        await call("POST", f"{path}/{row['id']}/void")
    for path, row in [(po_path, po), (so_path, so)]:
        source = await call("GET", f"{path}/{row['id']}")
        assert source["status"] == "confirmed" and Decimal(source["lines"][0]["qty_invoiced"]) == 0
    stock = await call("GET", f"/api/inventory/stock?warehouse_id={warehouse['id']}")
    assert not any(r["product_id"] == product["id"] for r in stock)
    current = await books()
    assert all(current.get(k, 0) == baseline.get(k, 0) for k in current.keys() | baseline.keys())
    print(
        "PASS: full buy/sell/pay cycle closes orders, clears GRNI/AR/AP "
        "and reverses to original stock/books",
        flush=True,
    )


async def precision_cycle(call, create, party, books, baseline):
    # Unit prices have six fractional places, while totals/payments have two.
    large = await create(
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": party["id"],
            "lines": [
                {
                    "description": "18-digit coefficient proof",
                    "qty": "10000",
                    "unit_price": "999999999999.999999",
                }
            ],
        },
    )
    large = await call("POST", f"/api/invoicing/invoices/{large['id']}/post")
    assert large["total"] == "9999999999999999.99"
    assert large["lines"][0]["unit_price"] == "999999999999.999999"
    pay = await create(
        "/api/invoicing/payments",
        {
            "direction": "in",
            "party_id": party["id"],
            "amount": large["total"],
            "method": "bank",
            "allocations": [{"invoice_id": large["id"], "amount": large["total"]}],
        },
    )
    assert pay["amount"] == "9999999999999999.99"
    assert (await call("GET", f"/api/invoicing/invoices/{large['id']}"))["status"] == "paid"
    await call("POST", f"/api/invoicing/payments/{pay['id']}/void")
    await call("POST", f"/api/invoicing/invoices/{large['id']}/void")
    current = await books()
    assert all(current.get(k, 0) == baseline.get(k, 0) for k in current.keys() | baseline.keys())
    print("PASS: 18-digit invoice/payment values round-trip and reverse exactly", flush=True)


async def main(failure_probe=False, trade_only=False, precision_only=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(
        base_url=URL, timeout=180, transport=httpx.AsyncHTTPTransport(retries=3)
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert login.status_code == 200, f"Login returned {login.status_code}"
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}

        async def send(method, path, body=None, *, key=None):
            headers = dict(auth)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            for attempt in range(3):
                try:
                    return await client.request(method, path, headers=headers, json=body)
                except httpx.TransportError:
                    if attempt == 2:
                        raise

        async def call(method, path, body=None, *, expected=200, key=None):
            response = await send(method, path, body, key=key)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}: {response.text[:500]}"
            )
            return response.json() if response.content else None

        async def create(path, body):
            return await call("POST", path, body, expected=201)

        party = await create(
            "/api/crm/contacts",
            {
                "name": f"Settlement proof {run}",
                "is_customer": True,
                "is_supplier": True,
            },
        )

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(r["total_debit"]) for r in rows) == sum(
                Decimal(r["total_credit"]) for r in rows
            )
            return {r["code"]: Decimal(r["balance"]) for r in rows}

        baseline = await books()

        async def restored():
            current = await books()
            assert all(
                current.get(k, 0) == baseline.get(k, 0) for k in current.keys() | baseline.keys()
            )

        async def rate(value):
            response = await send(
                "POST", "/api/currencies", {"code": "XTS", "name": "Testing currency"}
            )
            assert response.status_code in {201, 409}, response.text
            await call(
                "PUT",
                "/api/currencies/rates",
                {
                    "currency": "XTS",
                    "rate_date": datetime.now(UTC).date().isoformat(),
                    "rate": value,
                },
            )

        async def invoice(amount="100", *, post=True, **fields):
            row = await create(
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": party["id"],
                    "lines": [
                        {"description": "Worker proof service", "qty": "1", "unit_price": amount}
                    ],
                    **fields,
                },
            )
            return await action("invoices", row) if post else row

        async def action(kind, row, name="post", *, key=None, expected=200):
            return await call(
                "POST", f"/api/invoicing/{kind}/{row['id']}/{name}", key=key, expected=expected
            )

        async def view(row):
            return await call("GET", f"/api/invoicing/invoices/{row['id']}")

        async def payment(amount, target=None, **fields):
            return await create(
                "/api/invoicing/payments",
                {
                    "direction": "in",
                    "party_id": party["id"],
                    "amount": amount,
                    "method": "bank",
                    "allocations": [{"invoice_id": target["id"], "amount": amount}]
                    if target
                    else [],
                    **fields,
                },
            )

        if trade_only:
            await trade_cycle(call, create, party, books, baseline, run)
            return
        if precision_only:
            await precision_cycle(call, create, party, books, baseline)
            return

        if failure_probe:
            order = await create(
                "/api/sales/orders",
                {
                    "customer_id": party["id"],
                    "lines": [{"description": "Rollback source", "qty": "1", "unit_price": "1"}],
                },
            )
            await call("POST", f"/api/sales/orders/{order['id']}/confirm")
            draft = await invoice(post=False, source_order_id=order["id"], notes=PROBE)
            key = f"invoice-fault-{run}"
            await action("invoices", draft, expected=409, key=key)
            unchanged = await view(draft)
            assert unchanged["status"] == "draft" and unchanged["number"] is None
            source = await call("GET", f"/api/sales/orders/{order['id']}")
            assert Decimal(source["lines"][0]["qty_invoiced"]) == 0
            await restored()
            await call(
                "PATCH", f"/api/invoicing/invoices/{draft['id']}", {"notes": "Fault removed"}
            )
            posted = await action("invoices", draft, key=key)
            body = {
                "direction": "in",
                "party_id": party["id"],
                "amount": "1",
                "method": "bank",
                "reference": PROBE,
                "allocations": [{"invoice_id": posted["id"], "amount": "1"}],
            }
            key = f"payment-fault-{run}"
            await call("POST", "/api/invoicing/payments", body, expected=409, key=key)
            assert (await view(posted))["amount_paid"] == "0.00"
            body["reference"] = "Fault removed"
            pay = await call("POST", "/api/invoicing/payments", body, expected=201, key=key)
            replay = await call("POST", "/api/invoicing/payments", body, expected=201, key=key)
            assert replay == pay
            await action("payments", pay, "void")
            await action("invoices", posted, "void")
            await restored()
            print(
                "PASS: real D1 invoice/payment faults roll back progress, "
                "settlements, books and command claims",
                flush=True,
            )

        await rate("3")
        original = await invoice("0.05", currency="XTS")
        pays = [await payment(amount, original) for amount in ["0.02", "0.02", "0.01"]]
        assert (await view(original))["status"] == "paid"
        assert (await books())["1100"] == baseline["1100"]
        await action("payments", pays[0], "void")
        statement = await call("GET", f"/api/invoicing/statement/{party['id']}")
        assert statement["open_balance"] == "0.02" and statement["base_open_balance"] == "0.01"
        pays.append(await payment("0.02", original))
        for pay in pays[1:]:
            await action("payments", pay, "void")
        await action("invoices", original, "void")
        await restored()
        print(
            "PASS: final-cent FX settlements survive arbitrary payment void/reallocation",
            flush=True,
        )

        await rate("2")
        original = await invoice(currency="XTS")
        await rate("4")
        credit = await invoice("60", invoice_type="ar_credit", original_invoice_id=original["id"])
        pay = await payment("40", currency="XTS")
        await call(
            "POST",
            f"/api/invoicing/payments/{pay['id']}/allocate",
            {
                "allocations": [{"invoice_id": original["id"], "amount": "40"}],
            },
        )
        assert (await books())["1100"] == baseline["1100"]
        await rate("9")
        await action("payments", pay, "void")
        await action("invoices", credit, "void")
        await action("invoices", original, "void")
        await restored()
        print(
            "PASS: credit application, late allocation and exact reversal retain posted FX",
            flush=True,
        )

        original = await invoice()
        paid = await payment("100", original)
        credit = await invoice("60", invoice_type="ar_credit", original_invoice_id=original["id"])
        refunds = [
            await payment(amount, direction="out", credit_note_id=credit["id"])
            for amount in ["20", "40"]
        ]
        assert (await view(credit))["status"] == "paid"
        await action("invoices", credit, "void", expected=409)
        for refund in refunds:
            await action("payments", refund, "void")
        await action("invoices", credit, "void")
        await action("payments", paid, "void")
        await action("invoices", original, "void")
        await restored()
        print(
            "PASS: paid-invoice credit refunds cap, direction, void guards and complete reversal",
            flush=True,
        )

        original = await invoice()
        body = {
            "direction": "in",
            "party_id": party["id"],
            "amount": "60",
            "method": "bank",
            "allocations": [{"invoice_id": original["id"], "amount": "60"}],
        }
        key = f"concurrent-payment-{run}"
        replies = await asyncio.gather(
            *[
                call("POST", "/api/invoicing/payments", body, expected=201, key=key)
                for _ in range(6)
            ]
        )
        assert all(row == replies[0] for row in replies)
        assert (await view(original))["amount_paid"] == "60.00"
        # Distinct keys still compete for the authoritative remaining balance.
        body["amount"] = "30"
        body["allocations"][0]["amount"] = "30"
        contenders = await asyncio.gather(
            *[send("POST", "/api/invoicing/payments", body) for _ in range(2)]
        )
        assert sorted(r.status_code for r in contenders) == [201, 422]
        winner = next(r.json() for r in contenders if r.status_code == 201)
        assert (await view(original))["amount_paid"] == "90.00"
        await action("payments", winner, "void")
        await action("payments", replies[0], "void")
        await action("invoices", original, "void")
        await restored()
        print(
            "PASS: concurrent retries commit once and competing payments cannot overallocate",
            flush=True,
        )

        await precision_cycle(call, create, party, books, baseline)
        await trade_cycle(call, create, party, books, baseline, run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--failure-probe", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--trade-only", action="store_true")
    group.add_argument("--precision-only", action="store_true")
    options = parser.parse_args()
    asyncio.run(main(options.failure_probe, options.trade_only, options.precision_only))
