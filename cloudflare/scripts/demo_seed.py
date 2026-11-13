"""Resumable demo recipe driven entirely through the application's public API."""

from datetime import date, timedelta
from decimal import Decimal

VERSION = "cloudflare-demo-v1"


class DemoSeed:
    def __init__(self, client, credentials, checkpoint, save=lambda: None):
        self.client, self.credentials = client, credentials
        self.checkpoint, self.save = checkpoint, save
        self.completed = checkpoint.setdefault("completed", {})
        self.headers = {}

    async def login(self):
        credentials = self.credentials["admin"]
        response = await self.client.post(
            "/api/auth/login",
            json={key: credentials[key] for key in ["email", "password"]},
        )
        assert response.status_code == 200, f"Admin login returned {response.status_code}"
        self.headers = {"Authorization": "Bearer " + response.json()["access_token"]}

    async def read(self, path):
        response = await self.client.get(path, headers=self.headers)
        assert response.status_code == 200, f"GET {path}: {response.status_code}"
        return response.json()

    async def operation(self, key, path, body=None, *, method="POST", expected=201):
        if key in self.completed:
            return self.completed[key]
        self.checkpoint["pending"] = key
        self.save()
        headers = {**self.headers, "Idempotency-Key": VERSION + ":" + key}
        response = await self.client.request(method, path, headers=headers, json=body)
        if response.status_code == 401:
            await self.login()
            headers.update(self.headers)
            response = await self.client.request(method, path, headers=headers, json=body)
        assert response.status_code == expected, f"{key}: {method} {path}: {response.status_code}"
        self.completed[key] = response.json() if response.content else None
        self.checkpoint.pop("pending", None)
        self.save()
        return self.completed[key]

    async def action(self, key, path):
        return await self.operation(key, path, expected=200)

    async def run(self):
        if not self.headers:
            await self.login()
        await self.operation(
            "organization",
            "/api/org",
            method="PATCH",
            expected=200,
            body={
                "name": "Cloudflare ERP Demo",
                "legal_name": "Example Trading LLC",
                "country": "US",
            },
        )
        await self.users()
        await self.business()
        await self.hr()
        result = await self.verify()
        self.checkpoint["verified"] = result
        self.save()
        return result

    async def users(self):
        await self.operation(
            "employee-role",
            "/api/roles",
            {
                "code": "employee",
                "name": "Employee",
                "description": "Own leave requests only",
                "permission_codes": ["hr.leave.request"],
            },
        )
        for code, credentials in self.credentials["users"].items():
            role = (
                "employee" if code == "employee_peer" else "viewer" if code == "disabled" else code
            )
            user = await self.operation(
                "user-" + code,
                "/api/users",
                {**credentials, "role_codes": [role]},
            )
            if code == "disabled":
                await self.operation(
                    "disable-user",
                    "/api/users/" + user["id"],
                    {"is_active": False},
                    method="PATCH",
                    expected=200,
                )

    async def business(self):
        op, action = self.operation, self.action
        for key, name, customer, supplier in [
            ("customer", "Demo Customer", True, False),
            ("retail", "Nordic Retail AB", True, False),
            ("supplier", "Demo Supplier", False, True),
            ("dual", "Dual Trading Co", True, True),
        ]:
            await op(
                key,
                "/api/crm/contacts",
                {
                    "name": name,
                    "is_customer": customer,
                    "is_supplier": supplier,
                    "payment_terms_days": 30,
                    "tags": ["demo"],
                },
            )
        await op("uom", "/api/catalog/uoms", {"code": "pcs", "name": "Pieces"})
        category = await op("category", "/api/catalog/categories", {"name": "Electronics"})
        await op("accessories", "/api/catalog/categories", {"name": "Accessories"})
        tax = await op(
            "tax",
            "/api/catalog/taxes",
            {
                "code": "VAT20",
                "name": "VAT 20%",
                "rate_pct": "20",
                "applies_to": "both",
                "is_default_sale": True,
            },
        )
        for key, name, sale, cost, minimum in [
            ("cable", "USB-C Cable", "9.90", "4.20", "20"),
            ("mouse", "Wireless Mouse", "24.50", "12.00", "55"),
            ("keyboard", "Mechanical Keyboard", "89.00", "45.00", "5"),
        ]:
            await op(
                key,
                "/api/catalog/products",
                {
                    "name": name,
                    "sale_price": sale,
                    "cost_price": cost,
                    "min_stock": minimum,
                    "category_id": category["id"],
                    "uom_code": "pcs",
                    "sale_tax_id": tax["id"],
                    "purchase_tax_id": tax["id"],
                },
            )
        consulting = await op(
            "consulting",
            "/api/catalog/products",
            {
                "name": "Consulting Hour",
                "type": "service",
                "track_inventory": False,
                "sale_price": "120",
                "sale_tax_id": tax["id"],
            },
        )
        await op(
            "warehouse",
            "/api/inventory/warehouses",
            {
                "code": "MAIN",
                "name": "Main warehouse",
                "is_default": True,
            },
        )
        supplier, customer, retail = [self.completed[k] for k in ["supplier", "customer", "retail"]]
        bills = []
        for key, qty, cost in [
            ("cable", "100", "4.20"),
            ("mouse", "60", "12"),
            ("keyboard", "30", "45"),
        ]:
            product = self.completed[key]
            order = await op(
                "po-" + key,
                "/api/purchasing/orders",
                {
                    "supplier_id": supplier["id"],
                    "notes": "Demo stock purchase",
                    "lines": [{"product_id": product["id"], "qty": qty, "unit_price": cost}],
                },
            )
            await action("confirm-po-" + key, f"/api/purchasing/orders/{order['id']}/confirm")
            receipt = await op(
                "receipt-" + key, "/api/inventory/receipts", {"source_po_id": order["id"]}
            )
            await action("post-receipt-" + key, f"/api/inventory/receipts/{receipt['id']}/post")
            bill = await op(
                "bill-" + key,
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ap",
                    "party_id": supplier["id"],
                    "source_order_id": order["id"],
                },
            )
            bills.append(
                await action("post-bill-" + key, f"/api/invoicing/invoices/{bill['id']}/post")
            )
        await self.payment("pay-cable", supplier, bills[0], bills[0]["total"], direction="out")
        await self.payment("pay-mouse", supplier, bills[1], "100", direction="out")
        for key, party, lines in [
            ("sale", customer, [("cable", "10"), ("mouse", "4")]),
            ("retail-sale", retail, [("keyboard", "3")]),
        ]:
            order = await op(
                key,
                "/api/sales/orders",
                {
                    "customer_id": party["id"],
                    "notes": "Demo completed delivery",
                    "lines": [{"product_id": self.completed[p]["id"], "qty": q} for p, q in lines],
                },
            )
            await action("confirm-" + key, f"/api/sales/orders/{order['id']}/confirm")
            delivery = await op(
                "delivery-" + key, "/api/inventory/deliveries", {"source_so_id": order["id"]}
            )
            await action("post-delivery-" + key, f"/api/inventory/deliveries/{delivery['id']}/post")
            invoice = await op(
                "invoice-" + key,
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": party["id"],
                    "source_order_id": order["id"],
                    "due_date": (
                        date.fromisoformat(self.checkpoint["date"]) - timedelta(days=7)
                    ).isoformat(),
                },
            )
            await action("post-invoice-" + key, f"/api/invoicing/invoices/{invoice['id']}/post")
        await self.payment("customer-payment", customer, self.completed["invoice-sale"], "60")
        service = await op(
            "service-invoice",
            "/api/invoicing/invoices",
            {
                "invoice_type": "ar",
                "party_id": retail["id"],
                "lines": [{"product_id": consulting["id"], "qty": "8"}],
            },
        )
        service = await action(
            "post-service-invoice", f"/api/invoicing/invoices/{service['id']}/post"
        )
        await self.payment("service-payment", retail, service, service["total"])
        packaging = await op(
            "packaging-bill",
            "/api/invoicing/invoices",
            {
                "invoice_type": "ap",
                "party_id": self.completed["dual"]["id"],
                "lines": [{"description": "Packaging materials", "qty": "1", "unit_price": "85"}],
            },
        )
        packaging = await action(
            "post-packaging", f"/api/invoicing/invoices/{packaging['id']}/post"
        )
        await self.payment(
            "packaging-payment",
            self.completed["dual"],
            packaging,
            packaging["total"],
            direction="out",
        )
        for key, kind, original, party, amount in [
            ("customer-credit", "ar_credit", self.completed["invoice-sale"], customer, "5"),
            ("supplier-credit", "ap_credit", bills[1], supplier, "10"),
        ]:
            credit = await op(
                key,
                "/api/invoicing/invoices",
                {
                    "invoice_type": kind,
                    "party_id": party["id"],
                    "original_invoice_id": original["id"],
                    "lines": [
                        {
                            "description": "Agreed price credit",
                            "qty": "1",
                            "unit_price": amount,
                            "tax_id": tax["id"],
                        }
                    ],
                },
            )
            await action("post-" + key, f"/api/invoicing/invoices/{credit['id']}/post")
        partial = await op(
            "partial-sale",
            "/api/sales/orders",
            {
                "customer_id": customer["id"],
                "notes": "Demo partial delivery and billing",
                "lines": [
                    {"product_id": self.completed[p]["id"], "qty": "5"} for p in ["cable", "mouse"]
                ],
            },
        )
        await action("confirm-partial", f"/api/sales/orders/{partial['id']}/confirm")
        delivery = await op(
            "partial-delivery",
            "/api/inventory/deliveries",
            {
                "source_so_id": partial["id"],
                "lines": [
                    {"product_id": self.completed[p]["id"], "qty": q}
                    for p, q in [("cable", "2"), ("mouse", "1")]
                ],
            },
        )
        await action("post-partial-delivery", f"/api/inventory/deliveries/{delivery['id']}/post")
        invoice = await op(
            "partial-invoice",
            "/api/invoicing/invoices",
            {
                "invoice_type": "ar",
                "party_id": customer["id"],
                "source_order_id": partial["id"],
            },
        )
        await op(
            "edit-partial-invoice",
            f"/api/invoicing/invoices/{invoice['id']}",
            {
                "lines": [
                    {
                        **{
                            k: line[k]
                            for k in [
                                "source_line_id",
                                "product_id",
                                "description",
                                "unit_price",
                                "discount_pct",
                                "tax_id",
                            ]
                        },
                        "qty": q,
                    }
                    for line, q in zip(invoice["lines"], ["2", "1"], strict=True)
                ]
            },
            method="PATCH",
            expected=200,
        )
        await action("post-partial-invoice", f"/api/invoicing/invoices/{invoice['id']}/post")
        adjustment = await op(
            "damage",
            "/api/inventory/adjustments",
            {
                "reason": "damage",
                "notes": "Two damaged mice",
                "lines": [{"product_id": self.completed["mouse"]["id"], "qty": "-2"}],
            },
        )
        await action("post-damage", f"/api/inventory/adjustments/{adjustment['id']}/post")
        await op(
            "draft-sale",
            "/api/sales/orders",
            {
                "customer_id": retail["id"],
                "notes": "Demo editable draft",
                "lines": [{"product_id": self.completed["keyboard"]["id"], "qty": "2"}],
            },
        )
        for key in ["draft-quote", "sent-quote"]:
            quote = await op(
                key,
                "/api/sales/quotations",
                {
                    "customer_id": retail["id"],
                    "valid_until": (
                        date.fromisoformat(self.checkpoint["date"]) + timedelta(days=14)
                    ).isoformat(),
                    "lines": [{"product_id": self.completed["mouse"]["id"], "qty": "6"}],
                },
            )
            if key == "sent-quote":
                await action("send-quote", f"/api/sales/quotations/{quote['id']}/send")

    async def payment(self, key, party, invoice, amount, *, direction="in"):
        return await self.operation(
            key,
            "/api/invoicing/payments",
            {
                "direction": direction,
                "party_id": party["id"],
                "amount": amount,
                "method": "bank",
                "allocations": [{"invoice_id": invoice["id"], "amount": amount}],
            },
        )

    async def hr(self):
        department = await self.operation(
            "department", "/api/hr/departments", {"name": "Operations"}
        )
        for key in ["employee", "employee_peer", "hr"]:
            user = self.completed["user-" + key]
            await self.operation(
                "staff-" + key,
                "/api/hr/employees",
                {
                    "full_name": user["full_name"],
                    "work_email": user["email"],
                    "user_id": user["id"],
                    "department_id": department["id"],
                    "position": "People manager" if key == "hr" else "Staff",
                },
            )
        annual = await self.operation(
            "annual", "/api/hr/leave-types", {"name": "Annual leave", "days_per_year": "20"}
        )
        await self.operation(
            "sick", "/api/hr/leave-types", {"name": "Sick leave", "days_per_year": "10"}
        )
        first = date.fromisoformat(self.checkpoint["date"]) + timedelta(days=14)
        first += timedelta(days=(-first.weekday()) % 7)
        for index, status in enumerate(["pending", "approved", "rejected", "cancelled"]):
            start = first + timedelta(weeks=index)
            row = await self.operation(
                "leave-" + status,
                "/api/hr/leave-requests",
                {
                    "employee_id": self.completed[
                        "staff-employee_peer" if status == "approved" else "staff-employee"
                    ]["id"],
                    "type_id": annual["id"],
                    "date_from": start.isoformat(),
                    "date_to": (start + timedelta(days=1)).isoformat(),
                    "reason": "Demo " + status + " request",
                },
            )
            if status != "pending":
                action = {"approved": "approve", "rejected": "reject", "cancelled": "cancel"}[
                    status
                ]
                await self.action(
                    "decide-" + status, f"/api/hr/leave-requests/{row['id']}/{action}"
                )
        await self.operation(
            "leave-peer-pending",
            "/api/hr/leave-requests",
            {
                "employee_id": self.completed["staff-employee_peer"]["id"],
                "type_id": annual["id"],
                "date_from": first.isoformat(),
                "date_to": (first + timedelta(days=1)).isoformat(),
                "reason": "Demo peer pending request",
            },
        )

    async def verify(self):
        stock = await self.read("/api/inventory/stock")
        quantities = {row["product_id"]: Decimal(row["qty_on_hand"]) for row in stock}
        for key, amount in [("cable", "88"), ("mouse", "53"), ("keyboard", "27")]:
            assert quantities[self.completed[key]["id"]] == Decimal(amount), key
        assert sum(Decimal(row["stock_value"]) for row in stock) == Decimal("2220.60")
        dashboard = await self.read("/api/reports/dashboard")
        assert Decimal(dashboard["open_ar"]) == Decimal("543.96"), dashboard["open_ar"]
        assert Decimal(dashboard["open_ap"]) == Decimal("2372.00"), dashboard["open_ap"]
        assert dashboard["low_stock_count"] == 1
        trial = await self.read("/api/accounting/trial-balance")
        assert sum(Decimal(row["total_debit"]) for row in trial) == sum(
            Decimal(row["total_credit"]) for row in trial
        )
        leave = await self.read("/api/hr/leave-requests")
        assert {row["status"] for row in leave} == {"pending", "approved", "rejected", "cancelled"}
        return {
            "version": VERSION,
            "stock_value": "2220.60",
            "open_ar": "543.96",
            "open_ap": "2372.00",
            "low_stock_count": 1,
            "journal_balanced": True,
            "leave_states": 4,
            "completed_steps": len(self.completed),
        }
