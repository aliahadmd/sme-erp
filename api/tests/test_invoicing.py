"""Invoicing tests — AR/AP cycle, payments, allocations, statuses."""


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def test_full_ar_cycle_with_partial_payment(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Invoice Buyer", "is_customer": True},
        )
    ).json()
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={
                "name": "Invoice Widget",
                "sale_price": "100.00",
                "type": "service",
                "track_inventory": False,
            },
        )
    ).json()

    # --- draft invoice (unnumbered)
    created = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(admin),
        json={
            "invoice_type": "ar",
            "party_id": customer["id"],
            "lines": [{"product_id": product["id"], "qty": "2", "unit_price": "100.00"}],
        },
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert invoice["status"] == "draft"
    assert invoice["number"] is None, "drafts are unnumbered"
    assert invoice["total"] == "200.00"

    # --- post → numbered + event state
    posted = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin)
    )
    assert posted.status_code == 200, posted.text
    posted = posted.json()
    assert posted["number"].startswith("INV-")
    assert posted["status"] == "posted"

    # --- 40% payment
    pay1 = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "80.00",
            "method": "bank",
            "allocations": [{"invoice_id": invoice["id"], "amount": "80.00"}],
        },
    )
    assert pay1.status_code == 201, pay1.text
    payment = pay1.json()
    assert payment["number"].startswith("PAY-")
    after_first = (
        await client.get(f"/api/invoicing/invoices/{invoice['id']}", headers=_auth(admin))
    ).json()
    assert after_first["status"] == "partial"
    assert after_first["amount_paid"] == "80.00"

    # --- remaining 60% closes it
    pay2 = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "120.00",
            "method": "cash",
            "allocations": [{"invoice_id": invoice["id"], "amount": "120.00"}],
        },
    )
    assert pay2.status_code == 201
    final = (
        await client.get(f"/api/invoicing/invoices/{invoice['id']}", headers=_auth(admin))
    ).json()
    assert final["status"] == "paid"
    assert final["amount_paid"] == "200.00"

    # --- overpayment rejected
    pay3 = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "10.00",
            "allocations": [{"invoice_id": invoice["id"], "amount": "10.00"}],
        },
    )
    assert pay3.status_code == 422

    # --- statement shows zero open balance
    statement = (
        await client.get(f"/api/invoicing/statement/{customer['id']}", headers=_auth(admin))
    ).json()
    assert statement["open_balance"] == "0.00"


async def test_invoice_from_sales_order_marks_invoiced(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "SO Invoice Buyer", "is_customer": True},
        )
    ).json()
    so = (
        await client.post(
            "/api/sales/orders",
            headers=_auth(admin),
            json={
                "customer_id": customer["id"],
                "lines": [
                    {
                        "product_id": None,
                        "description": "Consulting",
                        "qty": "3",
                        "unit_price": "50.00",
                    }
                ],
            },
        )
    ).json()
    # free-text lines allowed? product_id null + description + price
    await client.post(f"/api/sales/orders/{so['id']}/confirm", headers=_auth(admin))

    inv = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(admin),
        json={
            "invoice_type": "ar",
            "party_id": customer["id"],
            "source_order_id": so["id"],
        },
    )
    assert inv.status_code == 201, inv.text
    body = inv.json()
    assert body["source_number"] == so["number"]
    assert body["total"] == "150.00"
    assert len(body["lines"]) == 1

    posted = await client.post(f"/api/invoicing/invoices/{body['id']}/post", headers=_auth(admin))
    assert posted.status_code == 200
    so_view = (await client.get(f"/api/sales/orders/{so['id']}", headers=_auth(admin))).json()
    # fully-invoiced free-text order is complete → closed
    assert so_view["status"] in ("invoiced", "closed")


async def test_ap_cycle_with_bill_numbering(client):
    admin = await _admin(client)
    supplier = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Bill Receiver", "is_supplier": True},
        )
    ).json()

    created = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(admin),
        json={
            "invoice_type": "ap",
            "party_id": supplier["id"],
            "lines": [
                {"product_id": None, "description": "Rent", "qty": "1", "unit_price": "500.00"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    posted = await client.post(
        f"/api/invoicing/invoices/{created.json()['id']}/post", headers=_auth(admin)
    )
    bill = posted.json()
    assert bill["number"].startswith("BILL-")

    pay = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "out",
            "party_id": supplier["id"],
            "amount": "500.00",
            "method": "transfer",
            "allocations": [{"invoice_id": bill["id"], "amount": "500.00"}],
        },
    )
    assert pay.status_code == 201, pay.text
    assert pay.json()["number"].startswith("SPAY-")
    final = (await client.get(f"/api/invoicing/invoices/{bill['id']}", headers=_auth(admin))).json()
    assert final["status"] == "paid"


async def test_void_rules(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Void Tester", "is_customer": True},
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
                    {"product_id": None, "description": "Fee", "qty": "1", "unit_price": "10.00"}
                ],
            },
        )
    ).json()

    # draft cannot be voided
    draft_void = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/void", headers=_auth(admin)
    )
    assert draft_void.status_code == 409

    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))

    # posted without payments → void ok
    voided = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/void", headers=_auth(admin)
    )
    assert voided.status_code == 200
    assert voided.json()["status"] == "void"

    # paid invoice cannot be voided while payments allocated
    invoice2 = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "lines": [
                    {"product_id": None, "description": "Fee", "qty": "1", "unit_price": "5.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{invoice2['id']}/post", headers=_auth(admin))
    await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "5.00",
            "allocations": [{"invoice_id": invoice2["id"], "amount": "5.00"}],
        },
    )
    blocked = await client.post(
        f"/api/invoicing/invoices/{invoice2['id']}/void", headers=_auth(admin)
    )
    assert blocked.status_code == 409

    # void the payment → invoice returns to paid→posted flow (balance restored)
    payments = (await client.get("/api/invoicing/payments", headers=_auth(admin))).json()
    target = next(p for p in payments["items"] if p["amount"] == "5.00")
    void_pay = await client.post(
        f"/api/invoicing/payments/{target['id']}/void", headers=_auth(admin)
    )
    assert void_pay.status_code == 200
    restored = (
        await client.get(f"/api/invoicing/invoices/{invoice2['id']}", headers=_auth(admin))
    ).json()
    assert restored["amount_paid"] == "0.00"
    assert restored["status"] == "posted"


async def test_payment_direction_must_match_invoice_type(client):
    admin = await _admin(client)
    supplier = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Mismatch Supplier", "is_supplier": True},
        )
    ).json()
    bill = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ap",
                "party_id": supplier["id"],
                "lines": [
                    {"product_id": None, "description": "X", "qty": "1", "unit_price": "1.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{bill['id']}/post", headers=_auth(admin))
    wrong = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": supplier["id"],
            "amount": "1.00",
            "allocations": [{"invoice_id": bill["id"], "amount": "1.00"}],
        },
    )
    assert wrong.status_code == 422


async def test_duplicate_invoicing_of_order_rejected_and_autoclose(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Close Buyer", "is_customer": True},
        )
    ).json()
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={
                "name": "Close Widget",
                "sale_price": "10.00",
                "type": "goods",
                "track_inventory": True,
            },
        )
    ).json()
    await client.post(
        "/api/inventory/warehouses",
        headers=_auth(admin),
        json={"code": "CLOSEW", "name": "Close WH", "is_default": True},
    )
    supplier = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "Close Seller", "is_supplier": True},
        )
    ).json()
    po = (
        await client.post(
            "/api/purchasing/orders",
            headers=_auth(admin),
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "qty": "10", "unit_price": "1.00"}],
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

    so = (
        await client.post(
            "/api/sales/orders",
            headers=_auth(admin),
            json={
                "customer_id": customer["id"],
                "lines": [{"product_id": product["id"], "qty": "4"}],
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

    inv1 = (
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
    await client.post(f"/api/invoicing/invoices/{inv1['id']}/post", headers=_auth(admin))

    # Fully invoiced already → second invoice from the same order is rejected
    inv2 = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(admin),
        json={
            "invoice_type": "ar",
            "party_id": customer["id"],
            "source_order_id": so["id"],
        },
    )
    assert inv2.status_code == 409
    # closed orders reject invoicing outright; partially-invoiced ones report remaining
    assert (
        "fully invoiced" in inv2.json()["error"]["detail"]
        or "confirmed/processed" in (inv2.json()["error"]["detail"])
    )

    # Fully delivered + fully invoiced → order auto-closed
    so_view = (await client.get(f"/api/sales/orders/{so['id']}", headers=_auth(admin))).json()
    assert so_view["status"] == "closed"
    po_view = (await client.get(f"/api/purchasing/orders/{po['id']}", headers=_auth(admin))).json()
    assert po_view["status"] == "received"  # billed via standalone, not the PO — stays received


async def test_credit_note_flow_balances_books(client):
    from decimal import Decimal

    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "CN Buyer", "is_customer": True},
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
                    {"product_id": None, "description": "Svc", "qty": "1", "unit_price": "100.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))

    tb_mid = (await client.get("/api/accounting/trial-balance", headers=_auth(admin))).json()
    ar_before = next(
        Decimal(r["total_debit"]) - Decimal(r["total_credit"])
        for r in tb_mid
        if r["code"] == "1100"
    )

    credit = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar_credit",
                "party_id": customer["id"],
                "original_invoice_id": invoice["id"],
                "lines": [
                    {
                        "product_id": None,
                        "description": "Correction",
                        "qty": "1",
                        "unit_price": "100.00",
                    }
                ],
            },
        )
    ).json()
    assert credit["number"] is None  # credit notes are numbered at post time
    posted_credit = await client.post(
        f"/api/invoicing/invoices/{credit['id']}/post", headers=_auth(admin)
    )
    assert posted_credit.status_code == 200, posted_credit.text
    assert posted_credit.json()["number"].startswith("CRN-")

    tb_after = (await client.get("/api/accounting/trial-balance", headers=_auth(admin))).json()
    ar_after = next(
        Decimal(r["total_debit"]) - Decimal(r["total_credit"])
        for r in tb_after
        if r["code"] == "1100"
    )
    # Credit note reduces AR by the credited amount
    assert ar_after == ar_before - Decimal("100.00")
    total_debit = sum(Decimal(r["total_debit"]) for r in tb_after)
    total_credit = sum(Decimal(r["total_credit"]) for r in tb_after)
    assert total_debit == total_credit, "trial balance must stay balanced"


async def test_credit_over_original_total_rejected(client):
    admin = await _admin(client)
    customer = (
        await client.post(
            "/api/crm/contacts", headers=_auth(admin), json={"name": "OverCN", "is_customer": True}
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
                    {"product_id": None, "description": "Svc", "qty": "1", "unit_price": "50.00"}
                ],
            },
        )
    ).json()
    await client.post(f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin))
    over = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(admin),
        json={
            "invoice_type": "ar_credit",
            "party_id": customer["id"],
            "original_invoice_id": invoice["id"],
            "lines": [
                {"product_id": None, "description": "Too much", "qty": "1", "unit_price": "80.00"}
            ],
        },
    )
    assert over.status_code == 409


async def test_multi_currency_invoice_and_realized_fx(client):
    from decimal import Decimal

    admin = await _admin(client)
    # EUR with two rates: invoice at 0.80, payment at 0.90 → realized FX loss
    await client.post("/api/currencies", headers=_auth(admin), json={"code": "EUR", "name": "Euro"})
    await client.put(
        "/api/currencies/rates",
        headers=_auth(admin),
        json={"currency": "EUR", "rate_date": "2020-01-01", "rate": "0.80"},
    )
    await client.put(
        "/api/currencies/rates",
        headers=_auth(admin),
        json={"currency": "EUR", "rate_date": "2030-01-01", "rate": "0.90"},
    )
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers=_auth(admin),
            json={"name": "FX Buyer", "is_customer": True, "currency": "EUR"},
        )
    ).json()
    invoice = (
        await client.post(
            "/api/invoicing/invoices",
            headers=_auth(admin),
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "invoice_date": "2020-06-01",
                "currency": "EUR",
                "lines": [
                    {"product_id": None, "description": "Work", "qty": "100", "unit_price": "1.00"}
                ],
            },
        )
    ).json()
    assert invoice["currency"] == "EUR"
    assert invoice["fx_rate"] == "0.80000000"
    assert invoice["total_base"] == "125.00"  # 100 EUR / 0.8
    posted = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/post", headers=_auth(admin)
    )
    assert posted.status_code == 200

    tb_mid = (await client.get("/api/accounting/trial-balance", headers=_auth(admin))).json()
    ar_before = next(
        Decimal(r["total_debit"]) - Decimal(r["total_credit"])
        for r in tb_mid
        if r["code"] == "1100"
    )

    # Pay 100 EUR when the rate is 0.90 → base 111.11 vs AR 125 → FX loss 13.89
    payment = await client.post(
        "/api/invoicing/payments",
        headers=_auth(admin),
        json={
            "direction": "in",
            "party_id": customer["id"],
            "amount": "100.00",
            "method": "bank",
            "allocations": [{"invoice_id": invoice["id"], "amount": "100.00"}],
        },
    )
    assert payment.status_code == 201, payment.text

    tb = (await client.get("/api/accounting/trial-balance", headers=_auth(admin))).json()
    ar_after = next(
        Decimal(r["total_debit"]) - Decimal(r["total_credit"]) for r in tb if r["code"] == "1100"
    )
    assert ar_after == ar_before - Decimal("125.00"), "AR cleared at invoice snapshot rate"
    total_debit = sum(Decimal(r["total_debit"]) for r in tb)
    total_credit = sum(Decimal(r["total_credit"]) for r in tb)
    assert total_debit == total_credit, "trial balance must stay balanced"
    status = (
        await client.get(f"/api/invoicing/invoices/{invoice['id']}", headers=_auth(admin))
    ).json()["status"]
    assert status == "paid"
