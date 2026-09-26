"""Quotation lifecycle tests — draft → sent → accepted → converted."""


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def _quote(client, token: str) -> dict:
    return (
        await client.post(
            "/api/sales/quotations",
            headers=_auth(token),
            json={
                "customer_id": (
                    await client.post(
                        "/api/crm/contacts",
                        headers=_auth(token),
                        json={"name": "Quote Buyer", "is_customer": True},
                    )
                ).json()["id"],
                "valid_until": "2099-12-31",
                "lines": [
                    {
                        "product_id": None,
                        "description": "Setup fee",
                        "qty": "1",
                        "unit_price": "250.00",
                    },
                    {
                        "product_id": None,
                        "description": "Licenses",
                        "qty": "10",
                        "unit_price": "50.00",
                    },
                ],
            },
        )
    ).json()


async def test_quotation_full_lifecycle(client):
    token = await _admin(client)
    quote = await _quote(client, token)
    assert quote["number"].startswith("QT-")
    assert quote["status"] == "draft"
    assert quote["total"] == "750.00"

    sent = (
        await client.post(f"/api/sales/quotations/{quote['id']}/send", headers=_auth(token))
    ).json()
    assert sent["status"] == "sent"

    accepted = (
        await client.post(f"/api/sales/quotations/{quote['id']}/accept", headers=_auth(token))
    ).json()
    assert accepted["status"] == "accepted"

    converted = (
        await client.post(f"/api/sales/quotations/{quote['id']}/convert", headers=_auth(token))
    ).json()
    assert converted["quotation_number"] == quote["number"]
    so = (
        await client.get(f"/api/sales/orders/{converted['order_id']}", headers=_auth(token))
    ).json()
    assert so["status"] == "draft"
    assert len(so["lines"]) == 2
    assert so["total"] == "750.00"

    # Double conversion is blocked
    again = await client.post(f"/api/sales/quotations/{quote['id']}/convert", headers=_auth(token))
    assert again.status_code == 409


async def test_quotation_reject_and_cancel_paths(client):
    token = await _admin(client)
    q1 = await _quote(client, token)
    await client.post(f"/api/sales/quotations/{q1['id']}/send", headers=_auth(token))
    rejected = (
        await client.post(f"/api/sales/quotations/{q1['id']}/reject", headers=_auth(token))
    ).json()
    assert rejected["status"] == "rejected"
    # rejected is terminal
    accept_after = await client.post(
        f"/api/sales/quotations/{q1['id']}/accept", headers=_auth(token)
    )
    assert accept_after.status_code == 409

    q2 = await _quote(client, token)
    cancelled = (
        await client.post(f"/api/sales/quotations/{q2['id']}/cancel", headers=_auth(token))
    ).json()
    assert cancelled["status"] == "cancelled"


async def test_expired_quote_cannot_be_accepted(client):
    token = await _admin(client)
    quote = (
        await client.post(
            "/api/sales/quotations",
            headers=_auth(token),
            json={
                "customer_id": (
                    await client.post(
                        "/api/crm/contacts",
                        headers=_auth(token),
                        json={"name": "Expiry Buyer", "is_customer": True},
                    )
                ).json()["id"],
                "valid_until": "2020-01-01",
                "lines": [
                    {"product_id": None, "description": "X", "qty": "1", "unit_price": "5.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/sales/quotations/{quote['id']}/send", headers=_auth(token))
    # Reading the quote triggers lazy expiry
    viewed = (await client.get(f"/api/sales/quotations/{quote['id']}", headers=_auth(token))).json()
    assert viewed["status"] == "expired"
    accept = await client.post(f"/api/sales/quotations/{quote['id']}/accept", headers=_auth(token))
    assert accept.status_code == 409


async def test_quote_rbac_purchasing_cannot_read(client):
    admin = await _admin(client)
    await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "quotepurch@example.com",
            "password": "password123",
            "full_name": "QP",
            "role_codes": ["purchasing"],
        },
    )
    purchaser = (
        await client.post(
            "/api/auth/login",
            json={"email": "quotepurch@example.com", "password": "password123"},
        )
    ).json()["access_token"]
    response = await client.get("/api/sales/quotations", headers=_auth(purchaser))
    assert response.status_code == 403


async def test_numbering_admin_endpoints(client):
    admin = await _admin(client)
    listed = (await client.get("/api/numbering", headers=_auth(admin))).json()
    entities = {row["entity"] for row in listed}
    assert {"quotation", "sales_order", "ar_invoice"} <= entities

    # Prefix update
    updated = await client.put(
        "/api/numbering/journal_entry",
        headers=_auth(admin),
        json={"value": {"prefix": "JV"}},
    )
    assert updated.status_code == 200
    assert updated.json()["prefix"] == "JV"

    # Unknown entity rejected
    bad = await client.put(
        "/api/numbering/nope", headers=_auth(admin), json={"value": {"prefix": "X"}}
    )
    assert bad.status_code == 404
