"""Inventory tests — costing, stock moves, receipts/deliveries, adjustments."""

import pytest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def inv() -> dict:
    return {}


async def _setup(client, token: str, inv: dict) -> None:
    """One-time master data for inventory tests (module-scoped within run)."""
    if "warehouse" in inv:
        return
    inv["warehouse"] = (
        await client.post("/api/inventory/warehouses", headers=_auth(token),
                          json={"code": "MAIN", "name": "Main warehouse", "is_default": True})
    ).json()["id"]
    inv["customer"] = (
        await client.post("/api/crm/contacts", headers=_auth(token),
                          json={"name": "Stock Buyer", "is_customer": True})
    ).json()["id"]
    inv["supplier"] = (
        await client.post("/api/crm/contacts", headers=_auth(token),
                          json={"name": "Stock Seller", "is_supplier": True})
    ).json()["id"]
    inv["product"] = (
        await client.post("/api/catalog/products", headers=_auth(token), json={
            "name": "Stocked Widget", "sale_price": "20.00", "cost_price": "10.00",
            "min_stock": "5",
        })
    ).json()["id"]


async def _confirmed_po(client, token: str, inv: dict, qty: str, price: str) -> str:
    po = (
        await client.post("/api/purchasing/orders", headers=_auth(token), json={
            "supplier_id": inv["supplier"],
            "lines": [{"product_id": inv["product"], "qty": qty, "unit_price": price}],
        })
    ).json()
    await client.post(f"/api/purchasing/orders/{po['id']}/confirm", headers=_auth(token))
    return po["id"]


async def _confirmed_so(client, token: str, inv: dict, qty: str, price: str) -> str:
    so = (
        await client.post("/api/sales/orders", headers=_auth(token), json={
            "customer_id": inv["customer"],
            "lines": [{"product_id": inv["product"], "qty": qty, "unit_price": price}],
        })
    ).json()
    await client.post(f"/api/sales/orders/{so['id']}/confirm", headers=_auth(token))
    return so["id"]


async def test_moving_average_costing(client, inv):
    admin = await _admin(client)
    await _setup(client, admin, inv)

    # Buy 10 @ 10.00
    po1 = await _confirmed_po(client, admin, inv, "10", "10.00")
    r1 = (await client.post("/api/inventory/receipts", headers=_auth(admin), json={
        "source_po_id": po1,
    })).json()
    assert r1["status"] == "draft" and len(r1["lines"]) == 1
    await client.post(f"/api/inventory/receipts/{r1['id']}/post", headers=_auth(admin))

    stock = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    row = next(r for r in stock if r["product_id"] == inv["product"])
    assert row["qty_on_hand"] == "10.0000"
    assert float(row["avg_cost"]) == 10.0
    # PO auto-moved to received
    po_view = (await client.get(f"/api/purchasing/orders/{po1}", headers=_auth(admin))).json()
    assert po_view["status"] == "received"

    # Buy 10 @ 14.00 → avg 12.00
    po2 = await _confirmed_po(client, admin, inv, "10", "14.00")
    r2 = (await client.post("/api/inventory/receipts", headers=_auth(admin), json={
        "source_po_id": po2,
    })).json()
    await client.post(f"/api/inventory/receipts/{r2['id']}/post", headers=_auth(admin))
    stock = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    row = next(r for r in stock if r["product_id"] == inv["product"])
    assert row["qty_on_hand"] == "20.0000"
    assert float(row["avg_cost"]) == 12.0
    assert float(row["stock_value"]) == 240.0


async def test_delivery_cogs_and_negative_guard(client, inv):
    admin = await _admin(client)
    await _setup(client, admin, inv)

    # Deliver 5 → COGS = 5 × 12.00 = 60.00
    so = await _confirmed_so(client, admin, inv, "5", "20.00")
    dlv = (await client.post("/api/inventory/deliveries", headers=_auth(admin), json={
        "source_so_id": so,
    })).json()
    posted = await client.post(f"/api/inventory/deliveries/{dlv['id']}/post", headers=_auth(admin))
    assert posted.status_code == 200, posted.text
    so_view = (await client.get(f"/api/sales/orders/{so}", headers=_auth(admin))).json()
    assert so_view["status"] == "delivered"

    moves = (await client.get(
        "/api/inventory/moves", headers=_auth(admin),
        params={"product_id": inv["product"], "limit": 5},
    )).json()
    delivery_move = next(m for m in moves["items"] if m["move_type"] == "delivery")
    assert delivery_move["qty"] == "-5.0000"
    assert float(delivery_move["cogs"]) == 60.0

    stock = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    row = next(r for r in stock if r["product_id"] == inv["product"])
    assert row["qty_on_hand"] == "15.0000"

    # Deliver more than on hand → 409 (negative stock guard)
    so_big = await _confirmed_so(client, admin, inv, "999", "20.00")
    dlv_big = (await client.post("/api/inventory/deliveries", headers=_auth(admin), json={
        "source_so_id": so_big,
    })).json()
    blocked = await client.post(
        f"/api/inventory/deliveries/{dlv_big['id']}/post", headers=_auth(admin)
    )
    assert blocked.status_code == 409
    assert "Insufficient stock" in blocked.json()["error"]["detail"]


async def test_adjustment_and_low_stock(client, inv):
    admin = await _admin(client)
    await _setup(client, admin, inv)

    stock_before = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    on_hand = float(next(r for r in stock_before if r["product_id"] == inv["product"])["qty_on_hand"])

    adj = (await client.post("/api/inventory/adjustments", headers=_auth(admin), json={
        "reason": "damage",
        "lines": [{"product_id": inv["product"], "qty": "-12"}],
    })).json()
    assert adj["number"].startswith("ADJ-")
    await client.post(f"/api/inventory/adjustments/{adj['id']}/post", headers=_auth(admin))

    stock = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    row = next(r for r in stock if r["product_id"] == inv["product"])
    assert float(row["qty_on_hand"]) == on_hand - 12
    assert row["is_low"] is (on_hand - 12 <= 5)

    low = (await client.get("/api/inventory/stock", headers=_auth(admin), params={"low_only": True})).json()
    assert any(r["product_id"] == inv["product"] for r in low)


async def test_receipt_void_reverses_stock(client, inv):
    admin = await _admin(client)
    await _setup(client, admin, inv)

    po = await _confirmed_po(client, admin, inv, "4", "5.00")
    r = (await client.post("/api/inventory/receipts", headers=_auth(admin), json={
        "source_po_id": po,
    })).json()
    await client.post(f"/api/inventory/receipts/{r['id']}/post", headers=_auth(admin))
    stock_after_post = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    qty_after_post = float(
        next(x for x in stock_after_post if x["product_id"] == inv["product"])["qty_on_hand"]
    )

    voided = await client.post(f"/api/inventory/receipts/{r['id']}/void", headers=_auth(admin))
    assert voided.json()["status"] == "void"
    stock_after_void = (await client.get("/api/inventory/stock", headers=_auth(admin))).json()
    qty_after_void = float(
        next(x for x in stock_after_void if x["product_id"] == inv["product"])["qty_on_hand"]
    )
    assert qty_after_void == qty_after_post - 4

    # double void rejected
    again = await client.post(f"/api/inventory/receipts/{r['id']}/void", headers=_auth(admin))
    assert again.status_code == 409


async def test_warehouse_rbac(client, inv):
    admin = await _admin(client)
    await _setup(client, admin, inv)
    # Viewer cannot post receipts
    await client.post("/api/users", headers=_auth(admin), json={
        "email": "stockviewer@example.com", "password": "password123",
        "full_name": "SV", "role_codes": ["viewer"],
    })
    viewer = (
        await client.post("/api/auth/login",
                          json={"email": "stockviewer@example.com", "password": "password123"})
    ).json()["access_token"]
    forbidden = await client.post(
        "/api/inventory/receipts", headers=_auth(viewer),
        json={"lines": [{"product_id": inv["product"], "qty": "1", "unit_cost": "1"}]},
    )
    assert forbidden.status_code == 403
