"""Accounting tests — CoA, auto-postings, trial balance integrity, manual entries."""

from decimal import Decimal


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def _trial_balance(client, token: str) -> list[dict]:
    return (await client.get("/api/accounting/trial-balance", headers=_auth(token))).json()


def _net(tb: list[dict], code: str) -> Decimal:
    row = next(r for r in tb if r["code"] == code)
    return Decimal(row["balance"])


async def _assert_balanced(tb: list[dict]) -> None:
    total_debit = sum(Decimal(r["total_debit"]) for r in tb)
    total_credit = sum(Decimal(r["total_credit"]) for r in tb)
    assert total_debit == total_credit, f"trial balance off: {total_debit} vs {total_credit}"


async def test_business_events_post_balanced_journals(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Acc Buyer", "is_customer": True},
        )
    ).json()
    supplier = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Acc Seller", "is_supplier": True},
        )
    ).json()
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={
                "name": "Acc Widget",
                "sale_price": "20.00",
                "cost_price": "10.00",
            },
        )
    ).json()
    (
        await client.post(
            "/api/inventory/warehouses",
            headers=_auth(admin),
            json={"code": "ACCW", "name": "Acc WH", "is_default": True},
        )
    ).json()

    tb_before = await _trial_balance(client, admin)

    # 1) Buy 10 @ 10, receive → inventory value 100 (avg 10)
    po = (
        await client.post(
            "/api/purchasing/orders",
            headers=_auth(admin),
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "qty": "10", "unit_price": "10.00"}],
            },
        )
    ).json()
    await client.post(f"/api/purchasing/orders/{po['id']}/confirm", headers=_auth(admin))
    receipt = (
        await client.post(
            "/api/inventory/receipts", headers=_auth(admin), json={"source_po_id": po["id"]}
        )
    ).json()
    await client.post(f"/api/inventory/receipts/{receipt['id']}/post", headers=_auth(admin))

    # 2) Sell 6 → delivery → COGS 60, inventory down 60
    so = (
        await client.post(
            "/api/sales/orders",
            headers=_auth(admin),
            json={
                "customer_id": customer["id"],
                "lines": [{"product_id": product["id"], "qty": "6", "unit_price": "20.00"}],
            },
        )
    ).json()
    await client.post(f"/api/sales/orders/{so['id']}/confirm", headers=_auth(admin))
    dlv = (
        await client.post(
            "/api/inventory/deliveries", headers=_auth(admin), json={"source_so_id": so["id"]}
        )
    ).json()
    await client.post(f"/api/inventory/deliveries/{dlv['id']}/post", headers=_auth(admin))

    # 3) AR invoice 6 × 20 = 120 → AR 120 / Revenue 100 / Tax 0 (none set)
    inv = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "source_order_id": so["id"],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{inv['id']}/post", headers=_auth(admin))

    # 4) Partial payment 120 (cash) → Cash 120 / AR 120
    await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "120.00",
            "method": "cash",
            "allocations": [{"invoice_id": inv["id"], "amount": "120.00"}],
        },
    )

    # 5) AP bill 30 → Purchases 30 / AP 30; pay it → AP 30 / Cash 30
    bill = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ap",
                "party_id": supplier["id"],
                "lines": [
                    {
                        "product_id": None,
                        "description": "Service",
                        "qty": "1",
                        "unit_price": "30.00",
                    }
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{bill['id']}/post", headers=_auth(admin))
    await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "out",
            "party_id": supplier["id"],
            "amount": "30.00",
            "method": "bank",
            "allocations": [{"invoice_id": bill["id"], "amount": "30.00"}],
        },
    )

    tb = await _trial_balance(client, admin)
    await _assert_balanced(tb)

    # Ledger effects of the whole cycle (vs baseline).
    # Note: receipts do not post journals in phase 1 (AP books at bill time);
    # the delivery COGS entry credits Inventory for the goods sold.
    assert _net(tb, "1200") - _net(tb_before, "1200") == Decimal("-60.00")  # COGS credit
    assert _net(tb, "5000") - _net(tb_before, "5000") == Decimal("60.00")  # COGS
    assert _net(tb, "4000") - _net(tb_before, "4000") == Decimal("-120.00")  # revenue (credit)
    assert _net(tb, "1000") - _net(tb_before, "1000") == Decimal("120.00")  # customer cash in
    assert _net(tb, "1010") - _net(tb_before, "1010") == Decimal("-30.00")  # supplier bank out
    assert _net(tb, "5100") - _net(tb_before, "5100") == Decimal("30.00")  # purchases
    # AP net zero (bill 30, paid 30); AR zero (invoiced 120, collected 120)
    assert _net(tb, "2000") - _net(tb_before, "2000") == Decimal("0.00")
    assert _net(tb, "1100") - _net(tb_before, "1100") == Decimal("0.00")

    # Journal browser shows the auto entries
    entries = (
        await client.get(
            "/api/accounting/journal-entries", headers=_auth(admin), params={"limit": 50}
        )
    ).json()
    sources = {e["source_type"] for e in entries}
    assert {"ar_invoice", "ap_invoice", "payment", "delivery"} <= sources
    for entry in entries:
        total_d = sum(Decimal(line["debit"]) for line in entry["lines"])
        total_c = sum(Decimal(line["credit"]) for line in entry["lines"])
        assert total_d == total_c, f"entry {entry['number']} unbalanced"


async def test_void_invoice_reverses(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Void Acc", "is_customer": True},
        )
    ).json()
    inv = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "lines": [
                    {"product_id": None, "description": "Fee", "qty": "1", "unit_price": "77.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{inv['id']}/post", headers=_auth(admin))
    tb_mid = await _trial_balance(client, admin)
    ar_after_post = _net(tb_mid, "1100")

    await client.post(f"/api/invoicing/invoices/{inv['id']}/void", headers=_auth(admin))
    tb_after = await _trial_balance(client, admin)
    ar_after_void = _net(tb_after, "1100")
    assert ar_after_void == ar_after_post - Decimal("77.00")
    await _assert_balanced(tb_after)


async def test_manual_entry_must_balance(client):
    admin = await _admin(client)
    accounts = (await client.get("/api/accounting/accounts", headers=_auth(admin))).json()
    cash = next(a for a in accounts if a["code"] == "1000")
    equity = next(a for a in accounts if a["code"] == "3000")

    unbalanced = await client.post(
        "/api/accounting/journal-entries",
        headers=_auth(admin),
        json={
            "memo": "owner invests (wrong)",
            "lines": [
                {"account_id": cash["id"], "debit": "100.00", "credit": "0"},
                {"account_id": equity["id"], "debit": "0", "credit": "90.00"},
            ],
        },
    )
    assert unbalanced.status_code == 422

    ok = await client.post(
        "/api/accounting/journal-entries",
        headers=_auth(admin),
        json={
            "memo": "owner invests",
            "lines": [
                {"account_id": cash["id"], "debit": "100.00", "credit": "0"},
                {"account_id": equity["id"], "debit": "0", "credit": "100.00"},
            ],
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["number"].startswith("JE-")

    tb = await _trial_balance(client, admin)
    await _assert_balanced(tb)


async def test_ledger_running_balance(client):
    admin = await _admin(client)
    accounts = (await client.get("/api/accounting/accounts", headers=_auth(admin))).json()
    cash = next(a for a in accounts if a["code"] == "1000")
    ledger = (
        await client.get(
            "/api/accounting/ledger", headers=_auth(admin), params={"account_id": cash["id"]}
        )
    ).json()
    assert len(ledger) >= 1
    # last running balance equals trial-balance balance
    tb = await _trial_balance(client, admin)
    assert Decimal(ledger[-1]["balance"]) == _net(tb, "1000")
