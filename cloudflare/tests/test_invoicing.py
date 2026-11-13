"""Financial acceptance beyond the original HTTP contract scenarios."""

import sqlite3
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.invoices import Invoices
from erp_cf.payments import Payments
from erp_cf.store import UnitOfWork
from tests.test_inventory import balances, setup


async def call(client, headers, method, path, body=None, *, expected=200, key=None):
    response = await client.request(
        method,
        "/api/invoicing/" + path,
        headers={**headers, **({"Idempotency-Key": key} if key else {})},
        json=body,
    )
    assert response.status_code == expected, response.text
    return response.json() if response.content else None


async def invoice(client, headers, party, amount="100", *, post=True, **fields):
    row = await call(
        client,
        headers,
        "POST",
        "invoices",
        {
            "invoice_type": "ar",
            "party_id": party["id"],
            "lines": [{"description": "Service", "qty": "1", "unit_price": amount}],
            **fields,
        },
        expected=201,
    )
    return await call(client, headers, "POST", f"invoices/{row['id']}/post") if post else row


async def payment(client, headers, party, amount, *, target=None, **fields):
    return await call(
        client,
        headers,
        "POST",
        "payments",
        {
            "direction": "in",
            "party_id": party["id"],
            "amount": amount,
            "method": "bank",
            "allocations": [{"invoice_id": target["id"], "amount": amount}] if target else [],
            **fields,
        },
        expected=201,
    )


async def rate(client, headers, value):
    await client.post("/api/currencies", headers=headers, json={"code": "EUR", "name": "Euro"})
    response = await client.put(
        "/api/currencies/rates",
        headers=headers,
        json={"currency": "EUR", "rate_date": datetime.now(UTC).date().isoformat(), "rate": value},
    )
    assert response.status_code == 200, response.text


def snapshot(env):
    return {
        table: [
            tuple(r) for r in env.DB.connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]
        for table in [
            "invoicing_invoices",
            "invoicing_payments",
            "invoicing_payment_allocations",
            "cf_settlement_effects",
            "core_document_sequences",
            "accounting_journal_entries",
            "accounting_journal_lines",
            "core_audit_logs",
            "cf_outbox",
            "cf_commands",
            "sales_sales_orders",
            "sales_sales_order_lines",
        ]
    }


def plan(env, key):
    actor = env.DB.connection.execute("SELECT id FROM core_users WHERE is_superuser=1").fetchone()[
        0
    ]
    return UnitOfWork(D1Database(env.DB), env.ORG_ID, actor, key, "test", {})


async def test_last_cent_clears_exact_base_after_arbitrary_payment_voids(client, env):
    headers, _, _, party = await setup(client)
    await rate(client, headers, "3")
    row = await invoice(client, headers, party, "0.05", currency="EUR")
    assert row["total_base"] == "0.02"
    pays = [
        await payment(client, headers, party, amount, target=row)
        for amount in ["0.02", "0.02", "0.01"]
    ]
    assert (await balances(client, headers))["1100"] == 0
    await call(client, headers, "POST", f"payments/{pays[0]['id']}/void")
    view = await call(client, headers, "GET", f"statement/{party['id']}")
    assert view["open_balance"] == "0.02" and view["base_open_balance"] == "0.01"
    pays.append(await payment(client, headers, party, "0.02", target=row))
    assert (await balances(client, headers))["1100"] == 0
    for pay in pays[1:]:
        await call(client, headers, "POST", f"payments/{pay['id']}/void")
    assert (await balances(client, headers))["1100"] == Decimal("0.02")
    await call(client, headers, "POST", f"invoices/{row['id']}/void")
    assert all(value == 0 for value in (await balances(client, headers)).values())
    for sql in [
        "UPDATE cf_settlement_effects SET amount_base=0",
        "DELETE FROM cf_settlement_effects",
        "UPDATE invoicing_payment_allocations SET amount=1",
        "DELETE FROM invoicing_payment_allocations",
        "UPDATE invoicing_invoices SET total=total+1",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            env.DB.connection.execute(sql)


async def test_credit_application_and_late_allocation_use_original_fx_and_reverse_exactly(client):
    headers, _, _, party = await setup(client)
    await rate(client, headers, "2")
    original = await invoice(client, headers, party, currency="EUR")
    await rate(client, headers, "4")
    credit = await invoice(
        client, headers, party, "60", invoice_type="ar_credit", original_invoice_id=original["id"]
    )
    assert (await balances(client, headers))["1100"] == Decimal("20")
    pay = await payment(client, headers, party, "40", currency="EUR")
    assert (await balances(client, headers))["1100"] == Decimal("10")
    await call(
        client,
        headers,
        "POST",
        f"payments/{pay['id']}/allocate",
        {"allocations": [{"invoice_id": original["id"], "amount": "40"}]},
    )
    assert (await balances(client, headers))["1100"] == 0
    await rate(client, headers, "9")
    await call(client, headers, "POST", f"payments/{pay['id']}/void")
    assert (await balances(client, headers))["1100"] == Decimal("20")
    await call(client, headers, "POST", f"invoices/{credit['id']}/void")
    assert (await balances(client, headers))["1100"] == Decimal("50")
    await call(client, headers, "POST", f"invoices/{original['id']}/void")
    assert all(value == 0 for value in (await balances(client, headers)).values())


async def test_each_payment_commit_stage_rolls_back_balances_books_and_receipt(client, env):
    headers, _, _, party = await setup(client)
    row = await invoice(client, headers, party)
    body = {
        "direction": "in",
        "party_id": party["id"],
        "amount": "30",
        "method": "bank",
        "allocations": [{"invoice_id": row["id"], "amount": "30"}],
    }
    before = snapshot(env)
    for target in [
        'INSERT INTO "invoicing_payments"',
        'INSERT INTO "invoicing_payment_allocations"',
        "INSERT INTO cf_settlement_effects",
        'UPDATE "invoicing_invoices"',
        'INSERT INTO "accounting_journal_lines"',
        'INSERT INTO "core_audit_logs"',
        "INSERT INTO cf_outbox",
    ]:

        async def fail(statements, target=target):
            index = next(i for i, stmt in enumerate(statements) if stmt.sql.startswith(target))
            statements.insert(
                index + 1,
                env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)"),
            )

        env.DB.before_batch = fail
        await call(client, headers, "POST", "payments", body, expected=409, key="payment-retry")
        env.DB.before_batch = None
        assert snapshot(env) == before
    paid = await call(client, headers, "POST", "payments", body, expected=201, key="payment-retry")
    replay = await call(
        client, headers, "POST", "payments", body, expected=201, key="payment-retry"
    )
    assert paid == replay
    assert (await call(client, headers, "GET", f"invoices/{row['id']}"))["amount_paid"] == "30.00"


async def test_competing_payment_and_fully_paid_credit_cap_plans_have_one_winner(client, env):
    headers, _, _, party = await setup(client)
    original = await invoice(client, headers, party)
    body = {
        "direction": "in",
        "party_id": party["id"],
        "amount": "60",
        "method": "bank",
        "allocations": [{"invoice_id": original["id"], "amount": "60"}],
    }
    plans = [plan(env, str(uuid.uuid4())) for _ in range(2)]
    for write in plans:
        await Payments(write).create({"body": body})
    await plans[0].commit({"paid": True})
    after = snapshot(env)
    with pytest.raises(ConflictError):
        await plans[1].commit({"paid": True})
    assert snapshot(env) == after
    await payment(client, headers, party, "40", target=original)
    credits = [
        await invoice(
            client,
            headers,
            party,
            "70",
            post=False,
            invoice_type="ar_credit",
            original_invoice_id=original["id"],
        )
        for _ in range(2)
    ]
    plans = [plan(env, str(uuid.uuid4())) for _ in range(2)]
    for write, row in zip(plans, credits, strict=True):
        await Invoices(write).post({"id": row["id"]})
    await plans[0].commit({"posted": True})
    after = snapshot(env)
    with pytest.raises(ConflictError):
        await plans[1].commit({"posted": True})
    assert snapshot(env) == after
    assert (await call(client, headers, "GET", f"invoices/{credits[1]['id']}"))["status"] == "draft"


async def test_mixed_currency_statement_and_rejected_cross_currency_allocation(client, env):
    headers, _, _, party = await setup(client)
    usd = await invoice(client, headers, party, "10")
    await rate(client, headers, "2")
    eur = await invoice(client, headers, party, "30", currency="EUR")
    await payment(client, headers, party, "5", currency="EUR")
    statement = await call(client, headers, "GET", f"statement/{party['id']}")
    assert statement["balances_by_currency"] == {"USD": "10.00", "EUR": "25.00"}
    assert statement["open_balance"] == statement["base_open_balance"] == "22.50"
    assert statement["currency"] == statement["base_currency"] == "USD"
    before = snapshot(env)
    await call(
        client,
        headers,
        "POST",
        "payments",
        {
            "party_id": party["id"],
            "direction": "in",
            "amount": "10",
            "method": "bank",
            "allocations": [
                {"invoice_id": usd["id"], "amount": "5"},
                {"invoice_id": eur["id"], "amount": "5"},
            ],
        },
        expected=422,
    )
    assert snapshot(env) == before


async def test_source_line_identity_survives_draft_edit_and_progress_is_released(client):
    headers, _, _, party = await setup(client)
    response = await client.post(
        "/api/sales/orders",
        headers=headers,
        json={
            "customer_id": party["id"],
            "lines": [
                {"description": "One", "qty": "2", "unit_price": "10"},
                {"description": "Two", "qty": "3", "unit_price": "20"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    order = response.json()
    await client.post(f"/api/sales/orders/{order['id']}/confirm", headers=headers)
    row = await invoice(client, headers, party, post=False, source_order_id=order["id"])
    edited = await call(
        client,
        headers,
        "PATCH",
        f"invoices/{row['id']}",
        {
            "lines": [
                {
                    "description": "Two edited",
                    "qty": "1",
                    "unit_price": "20",
                    "source_line_id": row["lines"][1]["source_line_id"],
                },
                {
                    "description": "One edited",
                    "qty": "1",
                    "unit_price": "10",
                    "source_line_id": row["lines"][0]["source_line_id"],
                },
            ]
        },
    )
    await call(client, headers, "POST", f"invoices/{edited['id']}/post")
    view = (await client.get(f"/api/sales/orders/{order['id']}", headers=headers)).json()
    assert [line["qty_invoiced"] for line in view["lines"]] == ["1.0000", "1.0000"]
    await call(client, headers, "POST", f"invoices/{edited['id']}/void")
    view = (await client.get(f"/api/sales/orders/{order['id']}", headers=headers)).json()
    assert [line["qty_invoiced"] for line in view["lines"]] == ["0.0000", "0.0000"]


async def test_invoice_post_faults_roll_back_source_progress_numbering_and_every_effect(
    client, env
):
    headers, _, product, party = await setup(client)
    response = await client.post(
        "/api/sales/orders",
        headers=headers,
        json={
            "customer_id": party["id"],
            "lines": [{"product_id": product["id"], "qty": "1", "unit_price": "10"}],
        },
    )
    assert response.status_code == 201, response.text
    order = response.json()
    await client.post(f"/api/sales/orders/{order['id']}/confirm", headers=headers)
    draft = await invoice(client, headers, party, post=False, source_order_id=order["id"])
    before = snapshot(env)
    for target in [
        'UPDATE "invoicing_invoices"',
        'UPDATE "sales_sales_order_lines"',
        'UPDATE "sales_sales_orders"',
        'INSERT INTO "accounting_journal_entries"',
        'INSERT INTO "accounting_journal_lines"',
        'INSERT INTO "core_audit_logs"',
        "INSERT INTO cf_outbox",
    ]:

        async def fail(statements, target=target):
            index = next(i for i, stmt in enumerate(statements) if stmt.sql.startswith(target))
            statements.insert(
                index + 1,
                env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)"),
            )

        env.DB.before_batch = fail
        await call(
            client,
            headers,
            "POST",
            f"invoices/{draft['id']}/post",
            expected=409,
            key="invoice-retry",
        )
        env.DB.before_batch = None
        assert snapshot(env) == before
    await call(client, headers, "POST", f"invoices/{draft['id']}/post", key="invoice-retry")


async def test_large_invoice_and_many_invoice_payment_respect_binding_limits(client):
    headers, _, _, party = await setup(client)
    rows = [await invoice(client, headers, party, "0.01") for _ in range(125)]
    allocations = [{"invoice_id": row["id"], "amount": "0.01"} for row in rows]
    pay = await payment(client, headers, party, "1.25", allocations=allocations)
    assert len(pay["allocations"]) == 125
    assert (await balances(client, headers))["1100"] == 0
    await call(client, headers, "POST", f"payments/{pay['id']}/void")
    assert (await balances(client, headers))["1100"] == Decimal("1.25")
    big = await invoice(
        client,
        headers,
        party,
        lines=[
            {"description": f"Service {i}", "qty": "1", "unit_price": "0.01"} for i in range(360)
        ],
    )
    assert len(big["lines"]) == 360 and big["total"] == "3.60"
    await call(client, headers, "POST", f"invoices/{big['id']}/void")
    assert (await balances(client, headers))["1100"] == Decimal("1.25")


async def test_complete_trade_verifier_reconciles_http_workflows(client):
    from scripts.verify_invoicing_remote import trade_cycle

    headers, _, _, party = await setup(client)

    async def request(method, path, body=None, *, expected=200, key=None):
        response = await client.request(method, path, headers=headers, json=body)
        assert response.status_code == expected, response.text
        return response.json() if response.content else None

    async def create(path, body):
        return await request("POST", path, body, expected=201)

    async def books():
        return await balances(client, headers)

    await trade_cycle(request, create, party, books, await books(), "localproof")


async def test_maximum_financial_coefficients_through_invoice_and_payment_http(client):
    from scripts.verify_invoicing_remote import precision_cycle

    headers, _, _, party = await setup(client)

    async def request(method, path, body=None, *, expected=200, key=None):
        response = await client.request(method, path, headers=headers, json=body)
        assert response.status_code == expected, response.text
        return response.json() if response.content else None

    async def create(path, body):
        return await request("POST", path, body, expected=201)

    async def books():
        return await balances(client, headers)

    await precision_cycle(request, create, party, books, await books())
