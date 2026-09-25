"""CRM + catalog module tests."""

CATEGORIES = ["catalog.category.read"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


# ---------------------------------------------------------------------- crm
async def test_contact_crud_and_flags(client):
    token = await _admin(client)

    created = await client.post(
        "/api/crm/contacts",
        headers=_auth(token),
        json={
            "name": "Acme GmbH",
            "is_customer": True,
            "is_supplier": True,
            "emails": [{"label": "work", "value": "hi@acme.example"}],
            "tags": ["wholesale"],
        },
    )
    assert created.status_code == 201, created.text
    contact = created.json()
    assert contact["code"].startswith("C-")
    assert contact["is_customer"] and contact["is_supplier"]

    # Appears in both customer and supplier listings
    customers = (await client.get("/api/crm/contacts?type=customer", headers=_auth(token))).json()
    suppliers = (await client.get("/api/crm/contacts?type=supplier", headers=_auth(token))).json()
    assert any(c["id"] == contact["id"] for c in customers["items"])
    assert any(c["id"] == contact["id"] for c in suppliers["items"])

    # Search
    found = (await client.get("/api/crm/contacts?q=acme", headers=_auth(token))).json()
    assert found["total"] >= 1

    # Update
    updated = await client.patch(
        f"/api/crm/contacts/{contact['id']}",
        headers=_auth(token),
        json={"payment_terms_days": 14},
    )
    assert updated.json()["payment_terms_days"] == 14

    # Archive hides from default listing
    await client.post(
        f"/api/crm/contacts/{contact['id']}/archive",
        headers=_auth(token),
        json={"archived": True},
    )
    default_list = (await client.get("/api/crm/contacts", headers=_auth(token))).json()
    assert all(c["id"] != contact["id"] for c in default_list["items"])


async def test_contact_requires_customer_or_supplier_flag(client):
    token = await _admin(client)
    response = await client.post("/api/crm/contacts", headers=_auth(token), json={"name": "Nobody"})
    assert response.status_code == 422


async def test_contact_creation_audited(client):
    token = await _admin(client)
    await client.post(
        "/api/crm/contacts", headers=_auth(token), json={"name": "Audit Co", "is_customer": True}
    )
    logs = (
        await client.get(
            "/api/audit-logs", headers=_auth(token), params={"entity_type": "crm.contact"}
        )
    ).json()
    assert logs["total"] >= 1


# ------------------------------------------------------------------ catalog
async def test_uom_tax_category_then_product_flow(client):
    token = await _admin(client)

    uom = (
        await client.post(
            "/api/catalog/uoms", headers=_auth(token), json={"code": "pcs", "name": "Pieces"}
        )
    ).json()
    assert uom["code"] == "PCS"

    tax = (
        await client.post(
            "/api/catalog/taxes",
            headers=_auth(token),
            json={"code": "vat18", "name": "VAT 18%", "rate_pct": 18, "applies_to": "both"},
        )
    ).json()
    assert float(tax["rate_pct"]) == 18.0

    category = (
        await client.post(
            "/api/catalog/categories", headers=_auth(token), json={"name": "Electronics"}
        )
    ).json()

    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(token),
            json={
                "name": "USB-C Cable",
                "uom_id": uom["id"],
                "category_id": category["id"],
                "sale_price": "9.90",
                "cost_price": "4.20",
                "sale_tax_id": tax["id"],
                "min_stock": "5",
            },
        )
    ).json()
    assert product["sku"], "sku should be auto-generated from name"
    assert product["track_inventory"] is True

    # Service cannot track inventory
    bad = await client.post(
        "/api/catalog/products",
        headers=_auth(token),
        json={
            "name": "Consulting hour",
            "type": "service",
            "track_inventory": True,
        },
    )
    assert bad.status_code == 422

    # Duplicate SKU conflicts
    dup = await client.post(
        "/api/catalog/products",
        headers=_auth(token),
        json={
            "name": "Another cable",
            "sku": product["sku"],
        },
    )
    assert dup.status_code == 409

    # Search + category filter
    listed = (
        await client.get("/api/catalog/products", headers=_auth(token), params={"q": "usb"})
    ).json()
    assert listed["total"] == 1

    # Category in use cannot be deleted
    delcat = await client.delete(f"/api/catalog/categories/{category['id']}", headers=_auth(token))
    assert delcat.status_code == 409


async def test_product_archive_and_rbac(client):
    admin = await _admin(client)
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={"name": "Archive me", "type": "service", "track_inventory": False},
        )
    ).json()
    archived = await client.post(
        f"/api/catalog/products/{product['id']}/archive?archived=true", headers=_auth(admin)
    )
    assert archived.json()["status"] == "archived"

    # Viewer role cannot create products
    await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "catalogviewer@example.com",
            "password": "password123",
            "full_name": "CV",
            "role_codes": ["viewer"],
        },
    )
    viewer_token = (
        await client.post(
            "/api/auth/login",
            json={"email": "catalogviewer@example.com", "password": "password123"},
        )
    ).json()["access_token"]
    forbidden = await client.post(
        "/api/catalog/products", headers=_auth(viewer_token), json={"name": "Nope"}
    )
    assert forbidden.status_code == 403
