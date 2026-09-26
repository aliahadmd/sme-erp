"""Notification wiring tests — the bell must receive real events."""


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def _notifications(client, token: str) -> list[dict]:
    page = (
        await client.get("/api/notifications", headers=_auth(token), params={"limit": 50})
    ).json()
    return page["items"]


async def test_invoice_posted_creates_notification(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Notify Buyer", "is_customer": True},
        )
    ).json()
    invoice = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "lines": [
                    {"product_id": None, "description": "Work", "qty": "1", "unit_price": "42.00"}
                ],
            },
        )
    ).json()
    before = await _notifications(client, admin)
    before_titles = [n["title"] for n in before]

    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))

    after = await _notifications(client, admin)
    new_titles = [n["title"] for n in after if n["title"] not in before_titles]
    assert any("Invoice INV-" in t and "posted" in t for t in new_titles), new_titles


async def test_payment_recorded_creates_notification(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Notify Payer", "is_customer": True},
        )
    ).json()
    invoice = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "lines": [
                    {"product_id": None, "description": "Work", "qty": "1", "unit_price": "10.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))
    await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "10.00",
            "method": "cash",
            "allocations": [{"invoice_id": invoice["id"], "amount": "10.00"}],
        },
    )
    notifications = await _notifications(client, admin)
    assert any(n["type"] == "payment_recorded" for n in notifications)


async def test_low_stock_notification_after_delivery(client):
    admin = await _admin(client)
    await client.post(
        "/api/inventory/warehouses",
        headers=_auth(admin),
        json={"code": "NOTIFYW", "name": "Notify WH", "is_default": True},
    )
    supplier = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Notify Seller", "is_supplier": True},
        )
    ).json()
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Notify Ship To", "is_customer": True},
        )
    ).json()
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={
                "name": "Notify Widget",
                "sale_price": "5.00",
                "cost_price": "2.00",
                "min_stock": "5",
            },
        )
    ).json()

    # Stock in: 6 units (min is 5)
    po = (
        await client.post(
            "/api/purchasing/orders",
            headers=_auth(admin),
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "qty": "6", "unit_price": "2.00"}],
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

    # Deliver 2 → 4 on hand, below minimum → low_stock notification expected
    so = (
        await client.post(
            "/api/sales/orders",
            headers=_auth(admin),
            json={
                "customer_id": customer["id"],
                "lines": [{"product_id": product["id"], "qty": "2"}],
            },
        )
    ).json()
    await client.post(f"/api/sales/orders/{so['id']}/confirm", headers=_auth(admin))
    delivery = (
        await client.post(
            "/api/inventory/deliveries", headers=_auth(admin), json={"source_so_id": so["id"]}
        )
    ).json()
    await client.post(f"/api/inventory/deliveries/{delivery['id']}/post", headers=_auth(admin))

    notifications = await _notifications(client, admin)
    low = [n for n in notifications if n["type"] == "low_stock"]
    assert any("Notify Widget" in n["title"] for n in low), [n["title"] for n in notifications]


async def test_overdue_invoice_lazy_detection_is_deduped(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Late Payer", "is_customer": True},
        )
    ).json()
    invoice = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "due_date": "2026-01-01",  # clearly overdue
                "lines": [
                    {
                        "product_id": None,
                        "description": "Old work",
                        "qty": "1",
                        "unit_price": "33.00",
                    }
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))

    # Polling the unread count triggers the lazy overdue check
    await client.get("/api/notifications/unread-count", headers=_auth(admin))
    first = await _notifications(client, admin)
    overdue_first = [n for n in first if n["type"] == "invoice_overdue"]
    assert any("overdue" in n["title"].lower() for n in overdue_first)

    # Second poll must not duplicate (dedupe per invoice)
    await client.get("/api/notifications/unread-count", headers=_auth(admin))
    second = await _notifications(client, admin)
    overdue_second = [n for n in second if n["type"] == "invoice_overdue"]
    assert len(overdue_second) == len(overdue_first)


async def test_mark_notification_read(client):
    admin = await _admin(client)
    await client.get("/api/notifications/unread-count", headers=_auth(admin))
    unread = (
        await client.get(
            "/api/notifications", headers=_auth(admin), params={"unread_only": "true", "limit": 1}
        )
    ).json()
    assert unread["total"] >= 1
    target = unread["items"][0]
    marked = await client.post(f"/api/notifications/{target['id']}/read", headers=_auth(admin))
    assert marked.json()["read_at"] is not None
