"""Regression tests for the 2026-09-30 audit findings.

Each test asserts the CORRECT behaviour for one finding (IDs in the names
match the audit report: C = critical, H = high, M = medium).
"""

import uuid
from decimal import Decimal

import pytest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _setup(client, t: str) -> dict:
    """Fresh parties/products per test (the test DB is shared per session)."""
    tag = _uid()
    await client.post(
        "/api/inventory/warehouses",
        headers=_auth(t),
        json={"code": "AUD", "name": "Audit", "is_default": True},
    )
    cust = await client.post(
        "/api/crm/contacts",
        headers=_auth(t),
        json={
            "name": f"Audit Cust {tag}",
            "is_customer": True,
            "emails": [{"value": "cust@example.com"}],
        },
    )
    supp = await client.post(
        "/api/crm/contacts",
        headers=_auth(t),
        json={"name": f"Audit Supp {tag}", "is_supplier": True},
    )
    prod = await client.post(
        "/api/catalog/products",
        headers=_auth(t),
        json={"name": f"Widget {tag}", "sale_price": "20", "cost_price": "10", "min_stock": "5"},
    )
    prod2 = await client.post(
        "/api/catalog/products",
        headers=_auth(t),
        json={"name": f"Gold {tag}", "sale_price": "200", "cost_price": "100"},
    )
    service = await client.post(
        "/api/catalog/products",
        headers=_auth(t),
        json={
            "name": f"Install {tag}",
            "sale_price": "50",
            "type": "service",
            "track_inventory": False,
        },
    )
    return {
        "cust": cust.json()["id"],
        "supp": supp.json()["id"],
        "prod": prod.json()["id"],
        "prod2": prod2.json()["id"],
        "service": service.json()["id"],
    }


async def _post(client, t: str, path: str, body: dict | None = None):
    response = await client.post(path, headers=_auth(t), json=body)
    assert response.status_code in (200, 201), f"{path}: {response.text}"
    return response.json()


async def _stock_in(client, t: str, s: dict, product: str, qty: str, price: str) -> tuple:
    po = await _post(
        client,
        t,
        "/api/purchasing/orders",
        {
            "supplier_id": s["supp"],
            "lines": [{"product_id": product, "qty": qty, "unit_price": price}],
        },
    )
    await _post(client, t, f"/api/purchasing/orders/{po['id']}/confirm")
    receipt = await _post(client, t, "/api/inventory/receipts", {"source_po_id": po["id"]})
    await _post(client, t, f"/api/inventory/receipts/{receipt['id']}/post")
    return po["id"], receipt["id"]


async def _so(client, t: str, s: dict, lines: list[dict]) -> str:
    so = await _post(client, t, "/api/sales/orders", {"customer_id": s["cust"], "lines": lines})
    await _post(client, t, f"/api/sales/orders/{so['id']}/confirm")
    return so["id"]


async def _invoice(client, t: str, party: str, amount: str, **extra) -> dict:
    body = {
        "invoice_type": extra.pop("invoice_type", "ar"),
        "party_id": party,
        "lines": [{"description": "x", "qty": "1", "unit_price": amount}],
        **extra,
    }
    invoice = await _post(client, t, "/api/invoicing/invoices", body)
    return await _post(client, t, f"/api/invoicing/invoices/{invoice['id']}/post")


async def _journals(client, t: str) -> list[dict]:
    response = await client.get(
        "/api/accounting/journal-entries", headers=_auth(t), params={"limit": 100}
    )
    return response.json()["items"]


async def _tb(client, t: str) -> dict[str, Decimal]:
    rows = (await client.get("/api/accounting/trial-balance", headers=_auth(t))).json()
    debit = sum(Decimal(r["total_debit"]) for r in rows)
    credit = sum(Decimal(r["total_credit"]) for r in rows)
    assert debit == credit, "trial balance must always balance"
    return {r["code"]: Decimal(r["balance"]) for r in rows}


def _delta(after: dict, before: dict, code: str) -> Decimal:
    return after.get(code, Decimal("0")) - before.get(code, Decimal("0"))


async def _get(client, t: str, path: str, **params):
    return (await client.get(path, headers=_auth(t), params=params)).json()


# ---------------------------------------------------------------- critical
async def test_c1_edited_draft_invoice_posts_balanced_journal(client):
    t = await _admin(client)
    s = await _setup(client, t)
    draft = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": s["cust"],
            "lines": [{"description": "x", "qty": "1", "unit_price": "100"}],
        },
    )
    edited = (
        await client.patch(
            f"/api/invoicing/invoices/{draft['id']}",
            headers=_auth(t),
            json={"lines": [{"description": "x", "qty": "2", "unit_price": "100"}]},
        )
    ).json()
    assert edited["total"] == "200.00" and edited["total_base"] == "200.00"
    await _post(client, t, f"/api/invoicing/invoices/{draft['id']}/post")
    assert any(e["source_id"] == draft["id"] for e in await _journals(client, t))


async def test_c1_patch_keeps_unsent_fields(client):
    t = await _admin(client)
    s = await _setup(client, t)
    draft = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": s["cust"],
            "due_date": "2030-01-31",
            "notes": "keep me",
            "lines": [{"description": "x", "qty": "1", "unit_price": "10"}],
        },
    )
    patched = (
        await client.patch(
            f"/api/invoicing/invoices/{draft['id']}", headers=_auth(t), json={"notes": "changed"}
        )
    ).json()
    assert patched["due_date"] == "2030-01-31" and patched["notes"] == "changed"


async def test_c2_failed_posting_rolls_back_document(client, monkeypatch):
    """A journal failure must abort the whole request — never a posted
    document without its entry."""
    from app.modules.accounting import postings

    t = await _admin(client)
    s = await _setup(client, t)
    draft = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": s["cust"],
            "lines": [{"description": "x", "qty": "1", "unit_price": "10"}],
        },
    )

    async def broken(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(postings, "_invoice_lines", broken)
    with pytest.raises(RuntimeError):
        await client.post(f"/api/invoicing/invoices/{draft['id']}/post", headers=_auth(t))
    monkeypatch.undo()
    after = await _get(client, t, f"/api/invoicing/invoices/{draft['id']}")
    assert after["status"] == "draft" and after["number"] is None


async def test_c3_receipt_debits_inventory_and_bill_clears_grni(client):
    t = await _admin(client)
    s = await _setup(client, t)
    before = await _tb(client, t)
    po_id, receipt_id = await _stock_in(client, t, s, s["prod"], "4", "10")
    assert any(e["source_id"] == receipt_id for e in await _journals(client, t))
    mid = await _tb(client, t)
    assert _delta(mid, before, "1200") == Decimal("40.00")
    assert _delta(mid, before, "2050") == Decimal("-40.00")
    bill = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {"invoice_type": "ap", "party_id": s["supp"], "source_order_id": po_id},
    )
    await _post(client, t, f"/api/invoicing/invoices/{bill['id']}/post")
    after = await _tb(client, t)
    assert _delta(after, before, "2050") == Decimal("0.00"), "GRNI cleared by the bill"
    assert _delta(after, before, "5100") == Decimal("0.00"), "goods are not expensed"
    assert _delta(after, before, "2000") == Decimal("-40.00")


async def test_c4_realized_fx_loss_on_payment(client):
    t = await _admin(client)
    import random
    import string

    tag = _uid()
    code = "USD"
    while code in ("USD", "EUR", "GBP"):  # a fresh, non-base code per run
        code = "".join(random.choices(string.ascii_uppercase, k=3))
    await client.post("/api/currencies", headers=_auth(t), json={"code": code, "name": "Test"})
    for rate_date, rate in (("2020-01-01", "0.80"), ("2021-01-01", "0.90")):
        await client.put(
            "/api/currencies/rates",
            headers=_auth(t),
            json={"currency": code, "rate_date": rate_date, "rate": rate},
        )
    cust = await _post(
        client,
        t,
        "/api/crm/contacts",
        {"name": f"FX {tag}", "is_customer": True, "currency": code},
    )
    invoice = await _invoice(client, t, cust["id"], "100", invoice_date="2020-06-01")
    assert invoice["currency"] == code and invoice["total_base"] == "125.00"
    before = await _tb(client, t)
    await _post(
        client,
        t,
        "/api/invoicing/payments",
        {
            "direction": "in",
            "party_id": cust["id"],
            "amount": "100",
            "method": "bank",
            "allocations": [{"invoice_id": invoice["id"], "amount": "100"}],
        },
    )
    after = await _tb(client, t)
    assert _delta(after, before, "1100") == Decimal("-125.00"), "AR cleared at invoice rate"
    assert _delta(after, before, "1010") == Decimal("111.11")
    assert _delta(after, before, "6900") == Decimal("13.89"), "realized FX loss"


async def test_c5_mixed_sign_adjustment_posts_journal(client):
    t = await _admin(client)
    s = await _setup(client, t)
    await _stock_in(client, t, s, s["prod2"], "5", "100")
    adjustment = await _post(
        client,
        t,
        "/api/inventory/adjustments",
        {
            "reason": "count",
            "lines": [
                {"product_id": s["prod"], "qty": "2"},
                {"product_id": s["prod2"], "qty": "-1"},
            ],
        },
    )
    await _post(client, t, f"/api/inventory/adjustments/{adjustment['id']}/post")
    assert any(e["source_id"] == adjustment["id"] for e in await _journals(client, t))


async def test_c6_second_full_credit_note_rejected_at_post(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "100")
    body = {
        "invoice_type": "ar_credit",
        "party_id": s["cust"],
        "original_invoice_id": invoice["id"],
    }
    c1 = await _post(client, t, "/api/invoicing/invoices", body)
    c2 = await _post(client, t, "/api/invoicing/invoices", body)
    await _post(client, t, f"/api/invoicing/invoices/{c1['id']}/post")
    second = await client.post(f"/api/invoicing/invoices/{c2['id']}/post", headers=_auth(t))
    assert second.status_code == 409


async def test_c7_duplicate_product_lines_invoiced_once(client):
    t = await _admin(client)
    s = await _setup(client, t)
    so = await _so(
        client,
        t,
        s,
        [
            {"product_id": s["prod"], "qty": "1", "unit_price": "20"},
            {"product_id": s["prod"], "qty": "1", "unit_price": "20"},
            {"description": "Setup fee", "qty": "1", "unit_price": "5"},
        ],
    )
    invoice = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {"invoice_type": "ar", "party_id": s["cust"], "source_order_id": so},
    )
    assert invoice["total"] == "45.00"
    await _post(client, t, f"/api/invoicing/invoices/{invoice['id']}/post")
    again = await client.post(
        "/api/invoicing/invoices",
        headers=_auth(t),
        json={"invoice_type": "ar", "party_id": s["cust"], "source_order_id": so},
    )
    assert again.status_code == 409, "free-text lines must not be billed twice"


# -------------------------------------------------------------------- high
async def test_h1_deliver_after_partial_invoice_and_bill_before_receipt(client):
    t = await _admin(client)
    s = await _setup(client, t)
    await _stock_in(client, t, s, s["prod"], "50", "10")
    so = await _so(client, t, s, [{"product_id": s["prod"], "qty": "10", "unit_price": "20"}])
    d1 = await _post(
        client,
        t,
        "/api/inventory/deliveries",
        {"source_so_id": so, "lines": [{"product_id": s["prod"], "qty": "5"}]},
    )
    await _post(client, t, f"/api/inventory/deliveries/{d1['id']}/post")
    inv = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {"invoice_type": "ar", "party_id": s["cust"], "source_order_id": so},
    )
    await _post(client, t, f"/api/invoicing/invoices/{inv['id']}/post")
    d2 = await _post(client, t, "/api/inventory/deliveries", {"source_so_id": so})
    await _post(client, t, f"/api/inventory/deliveries/{d2['id']}/post")
    assert (await _get(client, t, f"/api/sales/orders/{so}"))["status"] == "closed"

    po = await _post(
        client,
        t,
        "/api/purchasing/orders",
        {
            "supplier_id": s["supp"],
            "lines": [{"product_id": s["prod"], "qty": "3", "unit_price": "10"}],
        },
    )
    await _post(client, t, f"/api/purchasing/orders/{po['id']}/confirm")
    bill = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {"invoice_type": "ap", "party_id": s["supp"], "source_order_id": po["id"]},
    )
    await _post(client, t, f"/api/invoicing/invoices/{bill['id']}/post")
    receipt = await _post(client, t, "/api/inventory/receipts", {"source_po_id": po["id"]})
    await _post(client, t, f"/api/inventory/receipts/{receipt['id']}/post")
    assert (await _get(client, t, f"/api/purchasing/orders/{po['id']}"))["status"] == "closed"


async def test_h1_service_only_order_closes_when_invoiced(client):
    t = await _admin(client)
    s = await _setup(client, t)
    so = await _so(client, t, s, [{"product_id": s["service"], "qty": "1", "unit_price": "50"}])
    inv = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {"invoice_type": "ar", "party_id": s["cust"], "source_order_id": so},
    )
    await _post(client, t, f"/api/invoicing/invoices/{inv['id']}/post")
    assert (await _get(client, t, f"/api/sales/orders/{so}"))["status"] == "closed"


async def test_h2_h3_invoice_email_queues_and_notification_survives(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "5")
    response = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/send-email", headers=_auth(t)
    )
    assert response.status_code == 200, response.text
    assert response.json()["to"] == "cust@example.com"
    notes = await _get(client, t, "/api/notifications", limit=50)
    assert any(invoice["number"] in n["title"] for n in notes["items"])


async def test_h4_journal_list_is_paginated(client):
    t = await _admin(client)
    page = await _get(client, t, "/api/accounting/journal-entries", limit=5)
    assert {"items", "total", "limit", "offset"} <= set(page)


async def test_h5_tax_summary_splits_output_and_input(client):
    t = await _admin(client)
    s = await _setup(client, t)
    tax = await _post(
        client,
        t,
        "/api/catalog/taxes",
        {"code": f"T{_uid()[:5]}", "name": "VAT 10", "rate_pct": "10"},
    )
    for invoice_type, party in (("ar", s["cust"]), ("ap", s["supp"])):
        draft = await _post(
            client,
            t,
            "/api/invoicing/invoices",
            {
                "invoice_type": invoice_type,
                "party_id": party,
                "lines": [
                    {"description": "x", "qty": "1", "unit_price": "100", "tax_id": tax["id"]}
                ],
            },
        )
        await _post(client, t, f"/api/invoicing/invoices/{draft['id']}/post")
    rows = await _get(client, t, "/api/reports/tax-summary", date_from="2020-01-01")
    directions = {r["direction"] for r in rows}
    assert {"output", "input"} <= directions


async def test_h6_currency_defaults_follow_party_and_org(client):
    t = await _admin(client)
    org = await _get(client, t, "/api/org")
    contact = await _post(
        client, t, "/api/crm/contacts", {"name": f"Cur {_uid()}", "is_customer": True}
    )
    assert contact["currency"] == org["base_currency"]
    invoice = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar",
            "party_id": contact["id"],
            "lines": [{"description": "x", "qty": "1", "unit_price": "1"}],
        },
    )
    assert invoice["currency"] == org["base_currency"]


async def test_h7_referenced_contact_cannot_be_deleted(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "1")
    response = await client.delete(f"/api/crm/contacts/{s['cust']}", headers=_auth(t))
    assert response.status_code == 409
    after = await _get(client, t, f"/api/invoicing/invoices/{invoice['id']}")
    assert after["party_id"] == s["cust"]


async def test_h8_void_delivery_reverses_cogs_and_order_progress(client):
    t = await _admin(client)
    s = await _setup(client, t)
    await _stock_in(client, t, s, s["prod"], "5", "10")
    so = await _so(client, t, s, [{"product_id": s["prod"], "qty": "2", "unit_price": "20"}])
    before = await _tb(client, t)
    delivery = await _post(client, t, "/api/inventory/deliveries", {"source_so_id": so})
    await _post(client, t, f"/api/inventory/deliveries/{delivery['id']}/post")
    await _post(client, t, f"/api/inventory/deliveries/{delivery['id']}/void")
    after = await _tb(client, t)
    assert _delta(after, before, "5000") == Decimal("0.00"), "COGS reversed"
    assert _delta(after, before, "1200") == Decimal("0.00")
    assert (await _get(client, t, f"/api/sales/orders/{so}"))["status"] == "confirmed"
    await _post(client, t, "/api/inventory/deliveries", {"source_so_id": so})


async def test_h8_invoice_void_releases_order_quantities(client):
    t = await _admin(client)
    s = await _setup(client, t)
    so = await _so(client, t, s, [{"product_id": s["service"], "qty": "1", "unit_price": "50"}])
    body = {"invoice_type": "ar", "party_id": s["cust"], "source_order_id": so}
    invoice = await _post(client, t, "/api/invoicing/invoices", body)
    await _post(client, t, f"/api/invoicing/invoices/{invoice['id']}/post")
    await _post(client, t, f"/api/invoicing/invoices/{invoice['id']}/void")
    assert (await _get(client, t, f"/api/sales/orders/{so}"))["status"] == "confirmed"
    await _post(client, t, "/api/invoicing/invoices", body)


async def test_h9_credit_note_settles_original_and_refund_clears_ar(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "100")
    credit = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar_credit",
            "party_id": s["cust"],
            "original_invoice_id": invoice["id"],
            "lines": [{"description": "partial", "qty": "1", "unit_price": "30"}],
        },
    )
    await _post(client, t, f"/api/invoicing/invoices/{credit['id']}/post")
    original = await _get(client, t, f"/api/invoicing/invoices/{invoice['id']}")
    assert original["status"] == "partial" and original["open_balance"] == "70.00"
    statement = await _get(client, t, f"/api/invoicing/statement/{s['cust']}")
    assert statement["open_balance"] == "70.00", "credit applied once, not twice"

    # Pay the rest, then a later full credit becomes a refundable balance.
    await _post(
        client,
        t,
        "/api/invoicing/payments",
        {
            "direction": "in",
            "party_id": s["cust"],
            "amount": "70",
            "allocations": [{"invoice_id": invoice["id"], "amount": "70"}],
        },
    )
    refundable = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar_credit",
            "party_id": s["cust"],
            "original_invoice_id": invoice["id"],
            "lines": [{"description": "goodwill", "qty": "1", "unit_price": "20"}],
        },
    )
    refundable = await _post(client, t, f"/api/invoicing/invoices/{refundable['id']}/post")
    assert refundable["open_balance"] == "20.00"
    before = await _tb(client, t)
    await _post(
        client,
        t,
        "/api/invoicing/payments",
        {
            "direction": "out",
            "party_id": s["cust"],
            "amount": "20",
            "credit_note_id": refundable["id"],
        },
    )
    after = await _tb(client, t)
    assert _delta(after, before, "1100") == Decimal("20.00"), "refund clears the AR credit"
    assert _delta(after, before, "4100") == Decimal("0.00"), "no second P&L hit"
    over = await client.post(
        "/api/invoicing/payments",
        headers=_auth(t),
        json={
            "direction": "out",
            "party_id": s["cust"],
            "amount": "1",
            "credit_note_id": refundable["id"],
        },
    )
    assert over.status_code == 422


# ------------------------------------------------------------------ medium
async def test_m1_on_account_payment_can_be_allocated_later(client):
    t = await _admin(client)
    s = await _setup(client, t)
    payment = await _post(
        client,
        t,
        "/api/invoicing/payments",
        {"direction": "in", "party_id": s["cust"], "amount": "60"},
    )
    assert payment["unallocated"] == "60.00"
    invoice = await _invoice(client, t, s["cust"], "40")
    statement = await _get(client, t, f"/api/invoicing/statement/{s['cust']}")
    assert statement["unapplied_payments"] == "60.00"
    assert statement["open_balance"] == "-20.00"
    allocated = await _post(
        client,
        t,
        f"/api/invoicing/payments/{payment['id']}/allocate",
        {"allocations": [{"invoice_id": invoice["id"], "amount": "40"}]},
    )
    assert allocated["unallocated"] == "20.00"
    assert (await _get(client, t, f"/api/invoicing/invoices/{invoice['id']}"))["status"] == "paid"


async def test_m1_allocation_rejects_other_party_invoice(client):
    t = await _admin(client)
    s = await _setup(client, t)
    other = await _setup(client, t)
    invoice = await _invoice(client, t, other["cust"], "10")
    response = await client.post(
        "/api/invoicing/payments",
        headers=_auth(t),
        json={
            "direction": "in",
            "party_id": s["cust"],
            "amount": "10",
            "allocations": [{"invoice_id": invoice["id"], "amount": "10"}],
        },
    )
    assert response.status_code == 422


async def test_m4_credit_notes_are_never_overdue(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "10", invoice_date="2020-01-01")
    credit = await _post(
        client,
        t,
        "/api/invoicing/invoices",
        {
            "invoice_type": "ar_credit",
            "party_id": s["cust"],
            "original_invoice_id": invoice["id"],
            "invoice_date": "2020-01-02",
            "due_date": "2020-01-03",
            "lines": [{"description": "partial", "qty": "1", "unit_price": "4"}],
        },
    )
    credit = await _post(client, t, f"/api/invoicing/invoices/{credit['id']}/post")
    await client.get("/api/notifications/unread-count", headers=_auth(t))
    notes = await _get(client, t, "/api/notifications", limit=100)
    overdue = [n for n in notes["items"] if n["type"] == "invoice_overdue"]
    assert any(invoice["number"] in n["title"] for n in overdue), "the invoice IS overdue"
    assert not any(credit["number"] in n["title"] for n in overdue)


async def test_m5_journal_uses_document_date(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "10", invoice_date="2026-01-15")
    entry = next(e for e in await _journals(client, t) if e["source_id"] == invoice["id"])
    assert entry["entry_date"] == "2026-01-15"


async def test_m6_leave_working_days_reject_and_cancel(client):
    t = await _admin(client)
    employee = await _post(client, t, "/api/hr/employees", {"full_name": f"Leaver {_uid()}"})
    leave_type = await _post(
        client, t, "/api/hr/leave-types", {"name": f"Annual {_uid()}", "days_per_year": "5"}
    )
    # Fri 2028-01-07 … Mon 2028-01-10 = 2 working days
    first = await _post(
        client,
        t,
        "/api/hr/leave-requests",
        {
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2028-01-07",
            "date_to": "2028-01-10",
        },
    )
    assert first["days"] == "2.0"
    rejected = await _post(client, t, f"/api/hr/leave-requests/{first['id']}/reject")
    assert rejected["status"] == "rejected"
    # A rejected request frees the dates; pending requests count toward the allowance.
    pending = await _post(
        client,
        t,
        "/api/hr/leave-requests",
        {
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2028-01-10",
            "date_to": "2028-01-14",
        },
    )
    over = await client.post(
        "/api/hr/leave-requests",
        headers=_auth(t),
        json={
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2028-02-01",
            "date_to": "2028-02-01",
        },
    )
    assert over.status_code == 422, "pending days count toward the allowance"
    cancelled = await _post(client, t, f"/api/hr/leave-requests/{pending['id']}/cancel")
    assert cancelled["status"] == "cancelled"
    # Next year has a fresh allowance.
    await _post(
        client,
        t,
        "/api/hr/leave-requests",
        {
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2029-01-08",
            "date_to": "2029-01-12",
        },
    )


async def test_m7_low_stock_includes_out_of_stock_products(client):
    t = await _admin(client)
    await _setup(client, t)
    product = await _post(
        client,
        t,
        "/api/catalog/products",
        {"name": f"Empty {_uid()}", "sale_price": "1", "cost_price": "1", "min_stock": "10"},
    )
    rows = await _get(client, t, "/api/inventory/stock", low_only="true")
    assert any(r["product_id"] == product["id"] and r["is_low"] for r in rows)


async def test_m8_base_currency_locked_once_documents_exist(client):
    t = await _admin(client)
    s = await _setup(client, t)
    await _invoice(client, t, s["cust"], "1")
    org = await _get(client, t, "/api/org")
    other = "EUR" if org["base_currency"] != "EUR" else "GBP"
    response = await client.patch("/api/org", headers=_auth(t), json={"base_currency": other})
    assert response.status_code == 422


async def test_m10_settings_reject_broken_mapping(client):
    t = await _admin(client)
    response = await client.put(
        "/api/settings/accounting.mapping",
        headers=_auth(t),
        json={"value": {"ar": "9999"}},
    )
    assert response.status_code == 422


async def test_order_with_moved_goods_cannot_be_cancelled(client):
    t = await _admin(client)
    s = await _setup(client, t)
    await _stock_in(client, t, s, s["prod"], "2", "10")
    so = await _so(client, t, s, [{"product_id": s["prod"], "qty": "1", "unit_price": "20"}])
    delivery = await _post(client, t, "/api/inventory/deliveries", {"source_so_id": so})
    await _post(client, t, f"/api/inventory/deliveries/{delivery['id']}/post")
    response = await client.post(f"/api/sales/orders/{so}/cancel", headers=_auth(t))
    assert response.status_code == 409


async def test_void_payment_reverses_exactly(client):
    t = await _admin(client)
    s = await _setup(client, t)
    invoice = await _invoice(client, t, s["cust"], "25")
    before = await _tb(client, t)
    payment = await _post(
        client,
        t,
        "/api/invoicing/payments",
        {
            "direction": "in",
            "party_id": s["cust"],
            "amount": "25",
            "allocations": [{"invoice_id": invoice["id"], "amount": "25"}],
        },
    )
    await _post(client, t, f"/api/invoicing/payments/{payment['id']}/void")
    after = await _tb(client, t)
    assert all(_delta(after, before, code) == 0 for code in after)
    assert (await _get(client, t, f"/api/invoicing/invoices/{invoice['id']}"))["status"] == "posted"


async def test_m6_self_service_employee_requests_own_leave(client):
    t = await _admin(client)
    tag = _uid()
    role = await _post(
        client,
        t,
        "/api/roles",
        {"code": f"self-{tag}", "name": "Self service", "permission_codes": ["hr.leave.request"]},
    )
    email = f"self-{tag}@example.com"
    user = await _post(
        client,
        t,
        "/api/users",
        {
            "email": email,
            "password": "password123",
            "full_name": "Self Service",
            "role_codes": [role["code"]],
        },
    )
    employee = await _post(
        client, t, "/api/hr/employees", {"full_name": "Self Service", "user_id": user["id"]}
    )
    duplicate = await client.post(
        "/api/hr/employees",
        headers=_auth(t),
        json={"full_name": "Again", "user_id": user["id"]},
    )
    assert duplicate.status_code == 409
    leave_type = await _post(
        client, t, "/api/hr/leave-types", {"name": f"Self {tag}", "days_per_year": "10"}
    )

    login = await client.post("/api/auth/login", json={"email": email, "password": "password123"})
    me_token = login.json()["access_token"]
    me = await _get(client, me_token, "/api/hr/employees/me")
    assert me["id"] == employee["id"]
    types = await _get(client, me_token, "/api/hr/leave-types")
    assert any(lt["id"] == leave_type["id"] for lt in types)
    forbidden = await client.get("/api/hr/employees", headers=_auth(me_token))
    assert forbidden.status_code == 403
    request = await _post(
        client,
        me_token,
        "/api/hr/leave-requests",
        {
            "employee_id": employee["id"],
            "type_id": leave_type["id"],
            "date_from": "2030-03-04",
            "date_to": "2030-03-05",
        },
    )
    own_list = await _get(client, me_token, "/api/hr/leave-requests")
    assert [r["id"] for r in own_list] == [request["id"]]
    cancelled = await _post(client, me_token, f"/api/hr/leave-requests/{request['id']}/cancel")
    assert cancelled["status"] == "cancelled"
