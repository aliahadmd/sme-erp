"""Demo data generator — drives the running API so journals, audit, and
statuses are identical to real usage. Idempotent: skips when demo data exists.

Usage: `make seed-demo` (requires the api service to be up).
"""

import os

import httpx

BASE = os.getenv("SEED_BASE_URL", "http://localhost:8000")


class Client:
    def __init__(self) -> None:
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_PASSWORD", "admin123")
        self.token = httpx.post(
            f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=30
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, params: dict | None = None):
        return httpx.get(f"{BASE}{path}", headers=self.headers, params=params, timeout=30).json()

    def post(self, path: str, body: dict | None = None, params: dict | None = None):
        response = httpx.post(
            f"{BASE}{path}", headers=self.headers, json=body, params=params, timeout=30
        )
        response.raise_for_status()
        return response.json()


def main() -> None:
    client = Client()

    existing = client.get("/api/crm/contacts", params={"q": "Demo Customer"})
    if existing["total"] > 0:
        print("Demo data already present — skipping (use make reset to start over)")
        return

    print("Seeding demo data…")

    # Parties
    customer = client.post(
        "/api/crm/contacts",
        {
            "name": "Demo Customer",
            "is_customer": True,
            "payment_terms_days": 14,
            "tags": ["demo"],
        },
    )
    customer2 = client.post(
        "/api/crm/contacts",
        {
            "name": "Nordic Retail AB",
            "is_customer": True,
            "payment_terms_days": 30,
        },
    )
    supplier = client.post(
        "/api/crm/contacts",
        {
            "name": "Demo Supplier",
            "is_supplier": True,
            "payment_terms_days": 30,
        },
    )
    both = client.post(
        "/api/crm/contacts",
        {
            "name": "Dual Trading Co",
            "is_customer": True,
            "is_supplier": True,
        },
    )

    # Catalog
    tax = client.post(
        "/api/catalog/taxes",
        {
            "code": "VAT20",
            "name": "VAT 20%",
            "rate_pct": 20,
            "applies_to": "both",
            "is_default_sale": True,
        },
    )
    client.post("/api/catalog/uoms", {"code": "pcs", "name": "Pieces"})
    client.post("/api/catalog/uoms", {"code": "kg", "name": "Kilogram"})
    category = client.post("/api/catalog/categories", {"name": "Electronics"})
    client.post("/api/catalog/categories", {"name": "Accessories"})
    products = [
        client.post(
            "/api/catalog/products",
            {
                "name": "USB-C Cable",
                "category_id": category["id"],
                "sale_price": "9.90",
                "cost_price": "4.20",
                "min_stock": "20",
                "sale_tax_id": tax["id"],
                "purchase_tax_id": tax["id"],
            },
        ),
        client.post(
            "/api/catalog/products",
            {
                "name": "Wireless Mouse",
                "category_id": category["id"],
                "sale_price": "24.50",
                "cost_price": "12.00",
                "min_stock": "10",
                "sale_tax_id": tax["id"],
                "purchase_tax_id": tax["id"],
            },
        ),
        client.post(
            "/api/catalog/products",
            {
                "name": "Mechanical Keyboard",
                "category_id": category["id"],
                "sale_price": "89.00",
                "cost_price": "45.00",
                "min_stock": "5",
                "sale_tax_id": tax["id"],
                "purchase_tax_id": tax["id"],
            },
        ),
        client.post(
            "/api/catalog/products",
            {
                "name": "Consulting Hour",
                "type": "service",
                "track_inventory": False,
                "sale_price": "120.00",
            },
        ),
    ]
    cable, mouse, keyboard, consulting = products

    # Warehouse + stock in
    client.post(
        "/api/inventory/warehouses",
        {
            "code": "MAIN",
            "name": "Main warehouse",
            "is_default": True,
        },
    )
    for product, qty, cost in (
        (cable, "100", "4.20"),
        (mouse, "60", "12.00"),
        (keyboard, "30", "45.00"),
    ):
        po = client.post(
            "/api/purchasing/orders",
            {
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "qty": str(qty), "unit_price": str(cost)}],
            },
        )
        client.post(f"/api/purchasing/orders/{po['id']}/confirm")
        receipt = client.post("/api/inventory/receipts", {"source_po_id": po["id"]})
        client.post(f"/api/inventory/receipts/{receipt['id']}/post")

    # Sell cycle 1: full flow
    so1 = client.post(
        "/api/sales/orders",
        {
            "customer_id": customer["id"],
            "notes": "Demo order — full cycle",
            "lines": [
                {"product_id": cable["id"], "qty": "10"},
                {"product_id": mouse["id"], "qty": "4"},
            ],
        },
    )
    client.post(f"/api/sales/orders/{so1['id']}/confirm")
    dlv1 = client.post("/api/inventory/deliveries", {"source_so_id": so1["id"]})
    client.post(f"/api/inventory/deliveries/{dlv1['id']}/post")
    inv1 = client.post(
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": customer["id"],
            "source_order_id": so1["id"],
        },
    )
    client.post(f"/api/invoicing/invoices/{inv1['id']}/post")
    client.post(
        "/api/invoicing/payments",
        {
            "direction": "in",
            "party_id": customer["id"],
            "amount": "60.00",
            "method": "bank",
            "allocations": [{"invoice_id": inv1["id"], "amount": "60.00"}],
        },
    )

    # Sell cycle 2: goods order, invoiced and open
    so2 = client.post(
        "/api/sales/orders",
        {
            "customer_id": customer2["id"],
            "lines": [{"product_id": keyboard["id"], "qty": "3"}],
        },
    )
    client.post(f"/api/sales/orders/{so2['id']}/confirm")
    dlv2 = client.post("/api/inventory/deliveries", {"source_so_id": so2["id"]})
    client.post(f"/api/inventory/deliveries/{dlv2['id']}/post")
    inv2 = client.post(
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": customer2["id"],
            "source_order_id": so2["id"],
        },
    )
    client.post(f"/api/invoicing/invoices/{inv2['id']}/post")

    # Standalone service invoice (no delivery needed for services)
    inv3 = client.post(
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": customer2["id"],
            "lines": [{"product_id": consulting["id"], "qty": "8"}],
        },
    )
    client.post(f"/api/invoicing/invoices/{inv3['id']}/post")

    # AP bill + supplier payment
    bill = client.post(
        "/api/invoicing/invoices",
        {
            "invoice_type": "ap",
            "party_id": both["id"],
            "lines": [{"description": "Packaging materials", "qty": "1", "unit_price": "85.00"}],
        },
    )
    client.post(f"/api/invoicing/invoices/{bill['id']}/post")
    client.post(
        "/api/invoicing/payments",
        {
            "direction": "out",
            "party_id": both["id"],
            "amount": "85.00",
            "method": "transfer",
            "allocations": [{"invoice_id": bill["id"], "amount": "85.00"}],
        },
    )

    # Stock adjustment (damage)
    adj = client.post(
        "/api/inventory/adjustments",
        {
            "reason": "damage",
            "lines": [{"product_id": mouse["id"], "qty": "-2"}],
        },
    )
    client.post(f"/api/inventory/adjustments/{adj['id']}/post")

    print("Demo data seeded:")
    print("  - 4 contacts, 4 products (with taxes, categories, uoms)")
    print("  - Buy cycle: 3 POs → receipts (stock in)")
    print("  - Sell cycle 1: SO → delivery → invoice → partial payment")
    print("  - Sell cycle 2: SO → delivery → open invoice")
    print("  - AP bill paid, stock adjustment posted")
    print("Log in as the admin to explore. Try Dashboard and Reports!")


if __name__ == "__main__":
    main()
