import asyncio
import sqlite3
import uuid
from decimal import Decimal

import pytest

from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.inventory_commands import DELIVERY, InventoryPlan
from erp_cf.store import UnitOfWork
from tests.test_auth import login


async def setup(client):
    headers = {"Authorization": "Bearer " + await login(client)}

    async def create(path, data):
        response = await client.post(path, headers=headers, json=data)
        assert response.status_code == 201, response.text
        return response.json()

    warehouse = await create(
        "/api/inventory/warehouses", {"code": "MAIN", "name": "Main", "is_default": True}
    )
    product = await create(
        "/api/catalog/products",
        {"name": "Widget", "sale_price": "20", "cost_price": "10", "min_stock": "5"},
    )
    party = await create(
        "/api/crm/contacts", {"name": "Stock party", "is_customer": True, "is_supplier": True}
    )
    return headers, warehouse, product, party


async def create(client, headers, kind, body):
    response = await client.post("/api/inventory/" + kind, headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def post(client, headers, kind, row, action="post", expected=200, key=None):
    response = await client.post(
        f"/api/inventory/{kind}/{row['id']}/{action}",
        headers={**headers, **({"Idempotency-Key": key} if key else {})},
    )
    assert response.status_code == expected, response.text
    return response.json()


async def stock(client, headers, product, warehouse, *, low=False):
    response = await client.get(
        "/api/inventory/stock",
        headers=headers,
        params={"warehouse_id": warehouse["id"], "low_only": low},
    )
    assert response.status_code == 200, response.text
    return next((r for r in response.json() if r["product_id"] == product["id"]), None)


async def receipt(client, headers, product, qty, cost):
    row = await create(
        client,
        headers,
        "receipts",
        {"lines": [{"product_id": product["id"], "qty": qty, "unit_cost": cost}]},
    )
    await post(client, headers, "receipts", row)
    return row


async def order(client, headers, product, party, *, purchase=False, qty="5", price="10"):
    path = "/api/purchasing/orders" if purchase else "/api/sales/orders"
    response = await client.post(
        path,
        headers=headers,
        json={
            "supplier_id" if purchase else "customer_id": party["id"],
            "lines": [{"product_id": product["id"], "qty": qty, "unit_price": price}],
        },
    )
    assert response.status_code == 201, response.text
    row = response.json()
    response = await client.post(f"{path}/{row['id']}/confirm", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def balances(client, headers):
    response = await client.get("/api/accounting/trial-balance", headers=headers)
    assert response.status_code == 200
    rows = response.json()
    assert sum(Decimal(r["total_debit"]) for r in rows) == sum(
        Decimal(r["total_credit"]) for r in rows
    )
    return {r["code"]: Decimal(r["balance"]) for r in rows}


def counts(env):
    return {
        table: env.DB.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in [
            "inventory_stock",
            "inventory_stock_moves",
            "cf_stock_move_states",
            "accounting_journal_entries",
            "accounting_journal_lines",
            "core_audit_logs",
            "cf_outbox",
            "cf_commands",
        ]
    }


async def test_repeated_product_lines_restore_prior_cost_and_books(client, env):
    headers, warehouse, product, _ = await setup(client)
    row = await create(
        client,
        headers,
        "receipts",
        {
            "lines": [
                {"product_id": product["id"], "qty": "10", "unit_cost": "10"},
                {"product_id": product["id"], "qty": "10", "unit_cost": "14"},
            ]
        },
    )
    await post(client, headers, "receipts", row, key="receipt-once")
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "20.0000" and current["avg_cost"] == "12.000000"
    assert (await balances(client, headers))["1200"] == Decimal("240")
    responses = await asyncio.gather(
        *[post(client, headers, "receipts", row, key="receipt-once") for _ in range(4)]
    )
    assert all(r["status"] == "posted" for r in responses)
    assert counts(env)["inventory_stock_moves"] == 2
    await post(client, headers, "receipts", row, action="void", key="receipt-undo")
    restored = await stock(client, headers, product, warehouse, low=True)
    assert restored["qty_on_hand"] == "0.0000" and restored["avg_cost"] == "0.000000"
    assert (await balances(client, headers))["1200"] == 0
    assert counts(env)["inventory_stock_moves"] == counts(env)["cf_stock_move_states"] == 4
    await post(client, headers, "receipts", row, action="void", expected=409)
    for sql in [
        "UPDATE inventory_stock_moves SET qty=0",
        "DELETE FROM inventory_stock_moves",
        "UPDATE cf_stock_move_states SET before_json='{}'",
        "UPDATE inventory_receipts SET status='draft'",
        "DELETE FROM inventory_receipt_lines",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            env.DB.connection.execute(sql)


async def test_void_receipts_remove_original_value_after_other_receipts(client):
    headers, warehouse, product, _ = await setup(client)
    first = await receipt(client, headers, product, "10", "10")
    second = await receipt(client, headers, product, "10", "14")
    await post(client, headers, "receipts", first, action="void")
    current = await stock(client, headers, product, warehouse)
    assert current["avg_cost"] == "14.000000" and current["stock_value"] == "140.00"
    assert (await balances(client, headers))["1200"] == Decimal("140")
    await post(client, headers, "receipts", second, action="void")
    assert (await balances(client, headers))["1200"] == 0
    assert await stock(client, headers, product, warehouse) is None


async def test_failure_at_each_stock_post_stage_rolls_back_all_effects(client, env):
    headers, warehouse, product, party = await setup(client)
    await receipt(client, headers, product, "1", "2")
    po = await order(client, headers, product, party, purchase=True, qty="4", price="5")
    row = await create(client, headers, "receipts", {"source_po_id": po["id"]})
    before = counts(env)
    before_stock = await stock(client, headers, product, warehouse)
    for target in [
        'UPDATE "inventory_stock"',
        'INSERT INTO "inventory_stock_moves"',
        "INSERT INTO cf_stock_move_states",
        'UPDATE "purchasing_purchase_order_lines"',
        'UPDATE "inventory_receipts"',
        'INSERT INTO "accounting_journal_entries"',
        'INSERT INTO "accounting_journal_lines"',
        'INSERT INTO "core_audit_logs"',
        "INSERT INTO cf_outbox",
    ]:

        async def fail(statements, target=target):
            index = next(i for i, s in enumerate(statements) if s.sql.startswith(target))
            statements.insert(
                index + 1,
                env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)"),
            )

        env.DB.before_batch = fail
        await post(client, headers, "receipts", row, expected=409, key="retry-post")
        env.DB.before_batch = None
        assert counts(env) == before
        assert await stock(client, headers, product, warehouse) == before_stock
        viewed = await client.get(f"/api/purchasing/orders/{po['id']}", headers=headers)
        assert (
            viewed.json()["status"] == "confirmed"
            and Decimal(viewed.json()["lines"][0]["qty_received"]) == 0
        )
    await post(client, headers, "receipts", row, key="retry-post")
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "5.0000" and current["avg_cost"] == "4.400000"
    assert (await balances(client, headers))["1200"] == Decimal("22")


async def test_competing_native_delivery_plans_have_one_complete_winner(client, env):
    headers, warehouse, product, party = await setup(client)
    await receipt(client, headers, product, "5", "10")
    drafts = []
    for _ in range(2):
        so = await order(client, headers, product, party, qty="3")
        drafts.append(await create(client, headers, "deliveries", {"source_so_id": so["id"]}))
    actor = env.DB.connection.execute("SELECT id FROM core_users WHERE is_superuser=1").fetchone()[
        0
    ]
    plans = [
        UnitOfWork(D1Database(env.DB), env.ORG_ID, actor, key, "post", {})
        for key in ["delivery-a", "delivery-b"]
    ]
    for write, draft in zip(plans, drafts, strict=True):
        await InventoryPlan(write, DELIVERY).post({"id": draft["id"]})
    await plans[0].commit({"posted": True})
    after_first = counts(env)
    with pytest.raises(ConflictError):
        await plans[1].commit({"posted": True})
    assert counts(env) == after_first
    assert (await stock(client, headers, product, warehouse))["qty_on_hand"] == "2.0000"
    losing = (
        await client.get(f"/api/inventory/deliveries/{drafts[1]['id']}", headers=headers)
    ).json()
    assert losing["status"] == "draft"
    await post(client, headers, "deliveries", drafts[1], expected=409)
    assert (await balances(client, headers))["1200"] == Decimal("20")
    assert (await balances(client, headers))["5000"] == Decimal("30")


async def test_delivery_void_restores_cost_progress_and_exact_original_journal(client):
    headers, warehouse, product, party = await setup(client)
    await receipt(client, headers, product, "5", "10")
    so = await order(client, headers, product, party, qty="3")
    delivery = await create(client, headers, "deliveries", {"source_so_id": so["id"]})
    await post(client, headers, "deliveries", delivery)
    # Intervening inbound cost must not reprice the delivery reversal.
    await receipt(client, headers, product, "2", "20")
    await post(client, headers, "deliveries", delivery, action="void")
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "7.0000" and current["avg_cost"] == "12.857143"
    assert current["stock_value"] == "90.00"
    books = await balances(client, headers)
    assert books["1200"] == Decimal("90") and books["5000"] == 0
    viewed = (await client.get(f"/api/sales/orders/{so['id']}", headers=headers)).json()
    assert viewed["status"] == "confirmed" and Decimal(viewed["lines"][0]["qty_delivered"]) == 0


async def test_exact_18_digit_costs_quantities_and_overflow_rollback(client, env):
    headers, warehouse, product, _ = await setup(client)
    original = await receipt(client, headers, product, "0.0001", "999999999999.999999")
    current = await stock(client, headers, product, warehouse)
    assert current["avg_cost"] == "999999999999.999999" and current["stock_value"] == "100000000.00"
    await post(client, headers, "receipts", original, action="void")
    await receipt(client, headers, product, "99999999999999.9999", "1")
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "99999999999999.9999"
    overflow = await create(
        client,
        headers,
        "receipts",
        {"lines": [{"product_id": product["id"], "qty": "0.0001", "unit_cost": "1"}]},
    )
    before = counts(env)
    await post(client, headers, "receipts", overflow, expected=422)
    assert counts(env) == before
    for qty in ["0.00001", "1e99"]:
        response = await client.post(
            "/api/inventory/adjustments",
            headers=headers,
            json={"lines": [{"product_id": product["id"], "qty": qty}]},
        )
        assert response.status_code == 422


async def test_low_stock_includes_never_stocked_and_negative_policy_is_guarded(client):
    headers, warehouse, product, _ = await setup(client)
    initial = await stock(client, headers, product, warehouse, low=True)
    assert initial["qty_on_hand"] == "0.0000" and initial["is_low"]
    draft = await create(
        client, headers, "deliveries", {"lines": [{"product_id": product["id"], "qty": "1"}]}
    )
    await post(client, headers, "deliveries", draft, expected=409)
    changed = await client.put(
        "/api/settings/inventory.guard", headers=headers, json={"value": {"allow_negative": True}}
    )
    assert changed.status_code == 200
    await post(client, headers, "deliveries", draft)
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "-1.0000" and current["stock_value"] == "-0.00"
    await post(client, headers, "deliveries", draft, action="void")
    assert await stock(client, headers, product, warehouse) is None


async def test_cross_org_and_service_stock_are_rejected_and_default_is_unique(client, env):
    headers, _, product, _ = await setup(client)
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/inventory/warehouses",
                headers=headers,
                json={"code": f"D{i}", "name": "Other", "is_default": True},
            )
            for i in range(3)
        ]
    )
    assert all(r.status_code == 201 for r in responses)
    assert (
        env.DB.connection.execute(
            "SELECT COUNT(*) FROM inventory_warehouses WHERE is_default=1"
        ).fetchone()[0]
        == 1
    )
    foreign = UnitOfWork(
        D1Database(env.DB), str(uuid.uuid7()), str(uuid.uuid7()), "foreign-warehouse", "test", {}
    )
    row = foreign.add("inventory_warehouses", {"code": "FOREIGN", "name": "Foreign"})
    await foreign.commit({"created": True})
    response = await client.post(
        "/api/inventory/receipts",
        headers=headers,
        json={"warehouse_id": row["id"], "lines": [{"product_id": product["id"], "qty": "1"}]},
    )
    assert response.status_code == 404
    service = await client.post(
        "/api/catalog/products",
        headers=headers,
        json={"name": "Service", "type": "service", "track_inventory": False},
    )
    response = await client.post(
        "/api/inventory/receipts",
        headers=headers,
        json={"lines": [{"product_id": service.json()["id"], "qty": "1"}]},
    )
    assert response.status_code == 422


async def test_partial_discounted_fx_receipts_restore_fifo_progress_and_cost(client):
    headers, warehouse, product, party = await setup(client)
    response = await client.put(
        "/api/currencies/rates",
        headers=headers,
        json={"currency": "EUR", "rate_date": "2026-01-01", "rate": "0.5"},
    )
    assert response.status_code == 200
    response = await client.post(
        "/api/purchasing/orders",
        headers=headers,
        json={
            "supplier_id": party["id"],
            "order_date": "2026-02-01",
            "currency": "EUR",
            "lines": [
                {"product_id": product["id"], "qty": "2", "unit_price": "10", "discount_pct": "10"},
                {"product_id": product["id"], "qty": "3", "unit_price": "20"},
            ],
        },
    )
    assert response.status_code == 201
    po = response.json()
    assert (
        await client.post(f"/api/purchasing/orders/{po['id']}/confirm", headers=headers)
    ).status_code == 200
    first = await create(
        client,
        headers,
        "receipts",
        {
            "source_po_id": po["id"],
            "lines": [{"product_id": product["id"], "qty": "1", "unit_cost": "18"}],
        },
    )
    await post(client, headers, "receipts", first)
    remaining = await create(client, headers, "receipts", {"source_po_id": po["id"]})
    assert [(r["qty"], r["unit_cost"]) for r in remaining["lines"]] == [
        ("1.0000", "18.000000"),
        ("3.0000", "40.000000"),
    ]
    await post(client, headers, "receipts", remaining)
    current = await stock(client, headers, product, warehouse)
    assert current["avg_cost"] == "31.200000" and current["stock_value"] == "156.00"
    assert (await balances(client, headers))["1200"] == Decimal("156")
    await post(client, headers, "receipts", remaining, action="void")
    viewed = (await client.get(f"/api/purchasing/orders/{po['id']}", headers=headers)).json()
    assert [r["qty_received"] for r in viewed["lines"]] == ["1.0000", "0.0000"]
    current = await stock(client, headers, product, warehouse)
    assert current["qty_on_hand"] == "1.0000" and current["avg_cost"] == "18.000000"
    await post(client, headers, "receipts", first, action="void")
    assert (await balances(client, headers))["1200"] == 0


async def test_failed_void_keeps_original_stock_journal_and_progress(client, env):
    headers, warehouse, product, party = await setup(client)
    po = await order(client, headers, product, party, purchase=True, qty="5")
    row = await create(client, headers, "receipts", {"source_po_id": po["id"]})
    await post(client, headers, "receipts", row)
    before = counts(env)
    original = await stock(client, headers, product, warehouse)

    async def fail(statements):
        statements.append(
            env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)")
        )

    env.DB.before_batch = fail
    await post(client, headers, "receipts", row, action="void", expected=409, key="void-retry")
    env.DB.before_batch = None
    assert counts(env) == before and await stock(client, headers, product, warehouse) == original
    viewed = (await client.get(f"/api/purchasing/orders/{po['id']}", headers=headers)).json()
    assert viewed["status"] == "received"
    assert (await balances(client, headers))["1200"] == Decimal("50")
    await post(client, headers, "receipts", row, action="void", key="void-retry")
    assert (await balances(client, headers))["1200"] == 0


async def test_large_distinct_product_receipt_uses_bounded_reference_queries(
    client, env, monkeypatch
):
    headers, _, _, _ = await setup(client)
    actor = env.DB.connection.execute("SELECT id FROM core_users WHERE is_superuser=1").fetchone()[
        0
    ]
    seed = UnitOfWork(D1Database(env.DB), env.ORG_ID, actor, "large-products", "test", {})
    products = [
        seed.add("catalog_products", {"sku": f"BIG-{i}", "name": f"Large {i}"}) for i in range(360)
    ]
    await seed.commit({"created": len(products)})
    draft = await create(
        client,
        headers,
        "receipts",
        {"lines": [{"product_id": p["id"], "qty": "1", "unit_cost": "1"} for p in products]},
    )
    calls = 0
    original_all, original_batch = D1Database.all, D1Database.batch

    async def counted_all(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_all(self, *args, **kwargs)

    async def counted_batch(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_batch(self, *args, **kwargs)

    monkeypatch.setattr(D1Database, "all", counted_all)
    monkeypatch.setattr(D1Database, "batch", counted_batch)
    await post(client, headers, "receipts", draft)
    assert calls < 40  # One-by-one product/stock reads would need over 700 calls.
    assert counts(env)["inventory_stock_moves"] == counts(env)["cf_stock_move_states"] == 360


async def test_consumed_receipt_cannot_leave_residual_value_at_zero_stock(client, env):
    headers, warehouse, product, _ = await setup(client)
    first = await receipt(client, headers, product, "10", "10")
    second = await receipt(client, headers, product, "10", "20")
    delivery = await create(
        client, headers, "deliveries", {"lines": [{"product_id": product["id"], "qty": "10"}]}
    )
    await post(client, headers, "deliveries", delivery)
    before = counts(env)
    await post(client, headers, "receipts", second, action="void", expected=409)
    assert counts(env) == before
    assert (await stock(client, headers, product, warehouse))["stock_value"] == "150.00"
    assert (await balances(client, headers))["1200"] == Decimal("150")
    await post(client, headers, "deliveries", delivery, action="void")
    await post(client, headers, "receipts", second, action="void")
    await post(client, headers, "receipts", first, action="void")
    assert (await balances(client, headers))["1200"] == 0
