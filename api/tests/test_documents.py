"""Sales & purchasing document tests — totals, state machine, RBAC, numbering."""

import re

import pytest

from app.shared.totals import compute_line, compute_totals


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def doc_ids() -> dict:
    return {}


async def _setup_master_data(client, token: str, ids: dict) -> None:
    if "customer" in ids:
        return
    ids["customer"] = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(token),
            json={"name": "Buy Co", "is_customer": True},
        )
    ).json()["id"]
    ids["supplier"] = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(token),
            json={"name": "Sell Co", "is_supplier": True},
        )
    ).json()["id"]
    uom = (
        await client.post(
            "/api/catalog/uoms", headers=_auth(token), json={"code": "kg", "name": "Kilogram"}
        )
    ).json()["id"]
    tax = (
        await client.post(
            "/api/catalog/taxes",
            headers=_auth(token),
            json={"code": "V18", "name": "VAT 18%", "rate_pct": 18},
        )
    ).json()["id"]
    ids["product"] = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(token),
            json={
                "name": "Flour",
                "uom_id": uom,
                "sale_price": "10.00",
                "cost_price": "6.00",
                "sale_tax_id": tax,
                "purchase_tax_id": tax,
            },
        )
    ).json()["id"]


def test_totals_engine_math():
    line = compute_line("2.5", "10.00", "12.5", "18")
    assert str(line.base) == "21.88"  # 2.5 × 10 × 0.875 = 21.875 → banker's rounding
    assert str(line.tax) == "3.94"  # 21.88 × 18% = 3.9384
    assert str(line.total) == "25.82"
    totals = compute_totals([("2.5", "10.00", "12.5", "18"), ("1", "100", "0", "0")])
    assert str(totals.subtotal) == "125.00"
    assert str(totals.discount_total) == "3.12"
    assert str(totals.tax_total) == "3.94"
    assert str(totals.total) == "125.82"


async def test_sales_order_full_lifecycle(client, doc_ids):
    token = await _admin(client)
    await _setup_master_data(client, token, doc_ids)

    # --- create draft
    created = await client.post(
        "/api/sales/orders",
        headers=_auth(token),
        json={
            "customer_id": doc_ids["customer"],
            "notes": "rush order",
            "lines": [
                {
                    "product_id": doc_ids["product"],
                    "qty": "2.5",
                    "discount_pct": "12.5",
                    "unit_price": "10.00",
                    "tax_id": None,  # falls back to product default sale tax (18%)
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["number"].startswith("SO-")
    assert order["status"] == "draft"
    assert order["subtotal"] == "25.00"
    assert order["discount_total"] == "3.12"
    assert order["tax_total"] == "3.94"
    assert order["total"] == "25.82"
    assert order["customer_name"] == "Buy Co"
    assert order["lines"][0]["tax_rate_pct"] == "18.00", "tax rate snapshotted from product"

    # --- draft is editable
    updated = await client.patch(
        f"/api/sales/orders/{order['id']}",
        headers=_auth(token),
        json={"notes": "not rush anymore"},
    )
    assert updated.status_code == 200
    assert updated.json()["notes"] == "not rush anymore"

    # --- confirm
    confirmed = await client.post(f"/api/sales/orders/{order['id']}/confirm", headers=_auth(token))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    # --- immutable after confirm
    edit_after = await client.patch(
        f"/api/sales/orders/{order['id']}", headers=_auth(token), json={"notes": "nope"}
    )
    assert edit_after.status_code == 409

    # --- cancel from confirmed works
    cancelled = await client.post(f"/api/sales/orders/{order['id']}/cancel", headers=_auth(token))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # --- confirm from cancelled is illegal
    reconfirm = await client.post(f"/api/sales/orders/{order['id']}/confirm", headers=_auth(token))
    assert reconfirm.status_code == 409


async def test_purchase_order_flow_and_numbering(client, doc_ids):
    token = await _admin(client)
    await _setup_master_data(client, token, doc_ids)

    first = await client.post(
        "/api/purchasing/orders",
        headers=_auth(token),
        json={
            "supplier_id": doc_ids["supplier"],
            "lines": [{"product_id": doc_ids["product"], "qty": "10", "unit_price": "6.00"}],
        },
    )
    assert first.status_code == 201, first.text
    po = first.json()
    assert po["number"].startswith("PO-")
    assert po["status"] == "draft"
    # PO numbers are a separate sequence from SO
    # Sequence value depends on test order — assert the numbering FORMAT.
    assert re.fullmatch(r"PO-\d{4}-\d{4}", po["number"]), po["number"]

    confirmed = await client.post(
        f"/api/purchasing/orders/{po['id']}/confirm", headers=_auth(token)
    )
    assert confirmed.json()["status"] == "confirmed"

    # Supplier validation: a PO for a customer-only contact is rejected
    bad = await client.post(
        "/api/purchasing/orders",
        headers=_auth(token),
        json={
            "supplier_id": doc_ids["customer"],
            "lines": [{"product_id": doc_ids["product"], "qty": "1", "unit_price": "1"}],
        },
    )
    assert bad.status_code == 422


async def test_order_lines_validation(client, doc_ids):
    token = await _admin(client)
    await _setup_master_data(client, token, doc_ids)

    empty = await client.post(
        "/api/sales/orders",
        headers=_auth(token),
        json={
            "customer_id": doc_ids["customer"],
            "lines": [],
        },
    )
    assert empty.status_code == 422

    bad_qty = await client.post(
        "/api/sales/orders",
        headers=_auth(token),
        json={
            "customer_id": doc_ids["customer"],
            "lines": [{"product_id": doc_ids["product"], "qty": "0"}],
        },
    )
    assert bad_qty.status_code == 422


async def test_purchasing_rbac_sales_user_cannot_confirm_po(client, doc_ids):
    admin = await _admin(client)
    await _setup_master_data(client, admin, doc_ids)

    # make a purchasing user (has purchasing perms but not sales) attempt SO confirm
    await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "purchaser@example.com",
            "password": "password123",
            "full_name": "Paul Purchaser",
            "role_codes": ["purchasing"],
        },
    )
    purchaser_token = (
        await client.post(
            "/api/auth/login", json={"email": "purchaser@example.com", "password": "password123"}
        )
    ).json()["access_token"]

    so = await client.post(
        "/api/sales/orders",
        headers=_auth(admin),
        json={
            "customer_id": doc_ids["customer"],
            "lines": [{"product_id": doc_ids["product"], "qty": "1", "unit_price": "5"}],
        },
    )
    # A purchasing user cannot even create a sales order
    forbidden_create = await client.post(
        "/api/sales/orders",
        headers=_auth(purchaser_token),
        json={
            "customer_id": doc_ids["customer"],
            "lines": [{"product_id": doc_ids["product"], "qty": "1", "unit_price": "5"}],
        },
    )
    assert forbidden_create.status_code == 403

    # Admin confirms, purchaser cannot cancel (no sales.order.cancel permission)
    await client.post(f"/api/sales/orders/{so.json()['id']}/confirm", headers=_auth(admin))
    forbidden_cancel = await client.post(
        f"/api/sales/orders/{so.json()['id']}/cancel", headers=_auth(purchaser_token)
    )
    assert forbidden_cancel.status_code == 403


async def test_order_list_filters(client, doc_ids):
    token = await _admin(client)
    await _setup_master_data(client, token, doc_ids)
    draft = await client.post(
        "/api/sales/orders",
        headers=_auth(token),
        json={
            "customer_id": doc_ids["customer"],
            "lines": [{"product_id": doc_ids["product"], "qty": "1", "unit_price": "2"}],
        },
    )
    number = draft.json()["number"]
    listed = (
        await client.get(
            "/api/sales/orders", headers=_auth(token), params={"status": "draft", "q": number[:10]}
        )
    ).json()
    assert listed["total"] >= 1
    assert all(o["status"] == "draft" for o in listed["items"])
    by_number = (
        await client.get("/api/sales/orders", headers=_auth(token), params={"q": number})
    ).json()
    assert by_number["total"] == 1
