import asyncio
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from erp_cf.db import D1Database
from erp_cf.document_jobs import expire_quotes
from erp_cf.documents import PURCHASE, QUOTE, SALES
from erp_cf.errors import ConflictError
from erp_cf.order_flow import OrderFlow
from erp_cf.store import Repository, UnitOfWork, tables
from tests.test_auth import login


async def setup(client):
    headers = {"Authorization": "Bearer " + await login(client)}
    party = await client.post(
        "/api/crm/contacts",
        headers=headers,
        json={"name": "Straße 客户", "is_customer": True, "is_supplier": True},
    )
    product = await client.post(
        "/api/catalog/products",
        headers=headers,
        json={"name": "Widget", "sale_price": "12.345678", "cost_price": "6.250000"},
    )
    assert party.status_code == product.status_code == 201
    return headers, party.json(), product.json()


def body(party, product=None, *, purchase=False):
    return {
        "supplier_id" if purchase else "customer_id": party["id"],
        "lines": [
            {
                "product_id": product["id"] if product else None,
                "description": "Fee",
                "qty": "2",
                "unit_price": "10",
            }
        ],
    }


def plan(env, key):
    actor = env.DB.connection.execute("SELECT id FROM core_users WHERE is_superuser=1").fetchone()[
        0
    ]
    return UnitOfWork(D1Database(env.DB), env.ORG_ID, actor, key, "test", {})


async def confirmed(client, headers, party, product, *, purchase=False, extra=False):
    data = body(party, product, purchase=purchase)
    if extra:
        data["lines"] += [
            {"product_id": product["id"], "qty": "3", "unit_price": "10"},
            {"description": "Service", "qty": "1", "unit_price": "5"},
        ]
    prefix = "/api/purchasing/orders" if purchase else "/api/sales/orders"
    created = await client.post(prefix, headers=headers, json=data)
    assert created.status_code == 201, created.text
    response = await client.post(f"{prefix}/{created.json()['id']}/confirm", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_dated_fx_unicode_search_and_catalog_snapshots(client, env):
    headers, party, product = await setup(client)
    for day, rate in [("2026-01-01", "2"), ("2026-03-01", "4")]:
        r = await client.put(
            "/api/currencies/rates",
            headers=headers,
            json={"currency": "EUR", "rate_date": day, "rate": rate},
        )
        assert r.status_code == 200, r.text
    data = body(party, product)
    data.update(order_date="2026-02-01", currency="EUR")
    data["lines"][0].pop("unit_price")
    response = await client.post("/api/sales/orders", headers=headers, json=data)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["total"] == "24.69" and order["lines"][0]["unit_price"] == "12.345678"
    repository = Repository(D1Database(env.DB), SALES.table, env.ORG_ID)
    saved = await repository.get(order["id"])
    assert saved["fx_rate"] == Decimal("2") and saved["total_base"] == Decimal("12.34")
    changed = await client.patch(
        f"/api/catalog/products/{product['id']}",
        headers=headers,
        json={"name": "New Widget", "sale_price": "50"},
    )
    assert changed.status_code == 200
    changed = await client.patch(
        f"/api/sales/orders/{order['id']}",
        headers=headers,
        json={"order_date": "2026-04-01", "notes": "New date"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["lines"][0]["product_name"] == "Widget"
    assert changed.json()["lines"][0]["unit_price"] == "12.345678"
    saved = await repository.get(order["id"])
    assert saved["fx_rate"] == Decimal("4") and saved["total_base"] == Decimal("6.17")
    found = await client.get(
        "/api/sales/orders", headers=headers, params={"q": "STRASSE 客户", "party_id": party["id"]}
    )
    assert found.json()["total"] == 1 and found.json()["items"][0]["id"] == order["id"]
    old_line = order["lines"][0]["id"]
    changed = await client.patch(
        f"/api/sales/orders/{order['id']}",
        headers=headers,
        json={"lines": [{"description": "Replacement", "qty": "1", "unit_price": "10.125"}]},
    )
    assert changed.status_code == 200 and changed.json()["total"] == "10.12"
    assert changed.json()["lines"][0]["id"] != old_line
    assert (
        env.DB.connection.execute(
            f"SELECT COUNT(*) FROM {SALES.lines} WHERE id=?", (old_line,)
        ).fetchone()[0]
        == 0
    )


async def test_line_rounding_bounds_and_missing_fx_reject_without_partial_writes(client, env):
    headers, party, product = await setup(client)
    base = env.DB.connection.execute("SELECT COUNT(*) FROM cf_commands").fetchone()[0]
    for line in [
        {"qty": "0.00001", "unit_price": "1"},
        {"qty": "1", "unit_price": "1e99"},
        {"qty": "100000000000000", "unit_price": "1"},
        {"qty": "99999999999999.9999", "unit_price": "999999999999.999999"},
    ]:
        data = {"customer_id": party["id"], "lines": [line]}
        response = await client.post("/api/sales/orders", headers=headers, json=data)
        assert response.status_code == 422, response.text
    data = body(party, product)
    data.update(currency="EUR", order_date="2020-01-01")
    assert (await client.post("/api/sales/orders", headers=headers, json=data)).status_code == 422
    assert env.DB.connection.execute("SELECT COUNT(*) FROM cf_commands").fetchone()[0] == base
    assert env.DB.connection.execute(f"SELECT COUNT(*) FROM {SALES.table}").fetchone()[0] == 0
    exact = await client.post(
        "/api/sales/orders",
        headers=headers,
        json={
            "customer_id": party["id"],
            "lines": [{"qty": "0.0001", "unit_price": "999999999999.999999"}],
        },
    )
    assert exact.status_code == 201, exact.text
    assert exact.json()["lines"][0]["unit_price"] == "999999999999.999999"
    assert exact.json()["total"] == "100000000.00"


async def accepted_quote(client, headers, party, product=None):
    created = await client.post(
        "/api/sales/quotations",
        headers=headers,
        json={**body(party, product), "valid_until": "2099-12-31"},
    )
    assert created.status_code == 201, created.text
    quote = created.json()
    for action in ["send", "accept"]:
        response = await client.post(
            f"/api/sales/quotations/{quote['id']}/{action}", headers=headers
        )
        assert response.status_code == 200, response.text
    return quote


async def test_conversion_rolls_back_all_effects_then_races_exactly_once(client, env):
    headers, party, product = await setup(client)
    quote = await accepted_quote(client, headers, party, product)
    # Catalog changes must not change agreed quote line snapshots on conversion.
    await client.patch(
        f"/api/catalog/products/{product['id']}",
        headers=headers,
        json={"sale_price": "500", "name": "Renamed"},
    )
    path = f"/api/sales/quotations/{quote['id']}/convert"
    stable = {**headers, "Idempotency-Key": "conversion-retry"}
    audit_count = env.DB.connection.execute("SELECT COUNT(*) FROM core_audit_logs").fetchone()[0]
    for target in [
        'INSERT INTO "sales_sales_orders"',
        'INSERT INTO "sales_sales_order_lines"',
        'UPDATE "sales_quotations"',
        'INSERT INTO "core_audit_logs"',
    ]:

        async def fail(statements, target=target):
            position = next(
                i for i, statement in enumerate(statements) if statement.sql.startswith(target)
            )
            statements.insert(
                position + 1,
                env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)"),
            )

        env.DB.before_batch = fail
        response = await client.post(path, headers=stable)
        assert response.status_code == 409, response.text
        assert env.DB.connection.execute(f"SELECT COUNT(*) FROM {SALES.table}").fetchone()[0] == 0
        assert (
            env.DB.connection.execute("SELECT COUNT(*) FROM core_audit_logs").fetchone()[0]
            == audit_count
        )
        current = await Repository(D1Database(env.DB), QUOTE.table, env.ORG_ID).get(quote["id"])
        assert current["status"] == "accepted" and current["converted_order_id"] is None
    env.DB.before_batch = None
    responses = await asyncio.gather(*[client.post(path, headers=stable) for _ in range(6)])
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["order_id"] for r in responses}) == 1
    converted = responses[0].json()
    assert converted["order_number"].endswith("0001")
    order = (await client.get(f"/api/sales/orders/{converted['order_id']}", headers=headers)).json()
    assert order["lines"][0]["product_name"] == "Widget" and order["total"] == quote["total"]
    assert (await client.post(path, headers=headers)).status_code == 409
    quote2 = await accepted_quote(client, headers, party)
    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/sales/quotations/{quote2['id']}/convert",
                headers={**headers, "Idempotency-Key": f"different-{i}"},
            )
            for i in range(5)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409]


@pytest.mark.parametrize("purchase", [False, True])
async def test_partial_progress_both_orders_and_releases_share_one_command_state(
    client, env, purchase
):
    headers, party, product = await setup(client)
    order = await confirmed(client, headers, party, product, purchase=purchase, extra=True)
    cfg = PURCHASE if purchase else SALES
    write = plan(env, "partial-progress")
    flow = OrderFlow(write, cfg)
    moved = await flow.change(order["id"], cfg.move, by_product=[(product["id"], Decimal(3))])
    assert [r[cfg.move] for r in moved["lines"]] == [Decimal(2), Decimal(1), Decimal(0)]
    assert moved["status"] == ("received" if purchase else "delivered")
    invoiced = await flow.change(
        order["id"], "qty_invoiced", by_line=[(order["lines"][2]["id"], Decimal(1))]
    )
    assert invoiced["status"] == "invoiced"
    await flow.change(order["id"], cfg.move, by_product=[(product["id"], Decimal(2))])
    closed = await flow.change(
        order["id"],
        "qty_invoiced",
        by_line=[(order["lines"][0]["id"], Decimal(2)), (order["lines"][1]["id"], Decimal(3))],
    )
    assert closed["status"] == "closed"
    await write.commit({"status": closed["status"]})
    stored = await Repository(D1Database(env.DB), cfg.table, env.ORG_ID).get(order["id"])
    assert stored["status"] == "closed"
    undo = plan(env, "release-progress")
    flow = OrderFlow(undo, cfg)
    invoiced = await flow.change(
        order["id"], cfg.move, by_product=[(product["id"], Decimal(1))], release=True
    )
    assert invoiced["status"] == "invoiced"
    await flow.change(
        order["id"],
        "qty_invoiced",
        by_line=[(r["id"], Decimal(r["qty"])) for r in order["lines"]],
        release=True,
    )
    final = await flow.change(
        order["id"], cfg.move, by_product=[(product["id"], Decimal(4))], release=True
    )
    assert final["status"] == "confirmed" and all(
        r[cfg.move] == 0 and r["qty_invoiced"] == 0 for r in final["lines"]
    )
    await undo.commit({"released": True})
    prefix = "/api/purchasing/orders" if purchase else "/api/sales/orders"
    outstanding = (await client.get(f"{prefix}/{order['id']}/outstanding", headers=headers)).json()
    assert Decimal(outstanding["outstanding"][product["id"]]) == Decimal(5)


async def test_competing_progress_and_confirmation_outbox_are_atomic(client, env):
    headers, party, product = await setup(client)
    order = await confirmed(client, headers, party, product)
    pending = [plan(env, key) for key in ["first-movement", "second-movement"]]
    for write in pending:
        await OrderFlow(write, SALES).change(
            order["id"], SALES.move, by_product=[(product["id"], Decimal(2))]
        )
        write.audit("post", "delivery", order["id"])
        write.emit("delivery.posted", {"order_id": order["id"]})
    await pending[0].commit({"posted": True})
    with pytest.raises(ConflictError):
        await pending[1].commit({"posted": True})
    assert (
        env.DB.connection.execute(
            "SELECT COUNT(*) FROM cf_outbox WHERE event_type='delivery.posted'"
        ).fetchone()[0]
        == 1
    )
    assert (
        await client.post(f"/api/sales/orders/{order['id']}/cancel", headers=headers)
    ).status_code == 409
    invalid = plan(env, "invalid-release")
    with pytest.raises(ConflictError):
        await OrderFlow(invalid, SALES).change(
            order["id"], SALES.move, by_product=[(product["id"], Decimal(3))], release=True
        )
    event = env.DB.connection.execute(
        "SELECT payload_json FROM cf_outbox WHERE event_type='sales_order.confirmed'"
    ).fetchone()[0]
    assert json.loads(event)["org_id"] == env.ORG_ID


async def test_quote_gross_rounding_and_bounded_expiration(client, env):
    headers, party, _ = await setup(client)
    data = {
        "customer_id": party["id"],
        "lines": [{"qty": "3", "unit_price": "0.335"}] * 2,
        "valid_until": "2020-01-01",
    }
    created = await client.post("/api/sales/quotations", headers=headers, json=data)
    assert created.status_code == 201, created.text
    quote = created.json()
    assert (
        quote["subtotal"] == "2.01"
        and quote["discount_total"] == "0.01"
        and quote["total"] == "2.00"
    )
    assert (
        await client.post(f"/api/sales/quotations/{quote['id']}/send", headers=headers)
    ).status_code == 200
    assert (
        await client.post(f"/api/sales/quotations/{quote['id']}/accept", headers=headers)
    ).status_code == 409
    write = plan(env, "quote-expiry")
    result = await expire_quotes(write, date(2026, 1, 1), limit=1)
    assert result["expired"] == 1
    await write.commit(result)
    viewed = (await client.get(f"/api/sales/quotations/{quote['id']}", headers=headers)).json()
    assert viewed["status"] == "expired"


async def test_confirmation_failure_rolls_back_event_and_status_then_replays(client, env):
    headers, party, product = await setup(client)
    created = await client.post("/api/sales/orders", headers=headers, json=body(party, product))
    identity = created.json()["id"]
    headers["Idempotency-Key"] = "confirm-stable"

    async def fail(statements):
        position = next(
            i for i, s in enumerate(statements) if s.sql.startswith("INSERT INTO cf_outbox")
        )
        statements.insert(
            position + 1,
            env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)"),
        )

    env.DB.before_batch = fail
    response = await client.post(f"/api/sales/orders/{identity}/confirm", headers=headers)
    assert response.status_code == 409, response.text
    env.DB.before_batch = None
    current = await Repository(D1Database(env.DB), SALES.table, env.ORG_ID).get(identity)
    assert current["status"] == "draft" and current["confirmed_at"] is None
    assert env.DB.connection.execute("SELECT COUNT(*) FROM cf_outbox").fetchone()[0] == 0
    responses = await asyncio.gather(
        *[client.post(f"/api/sales/orders/{identity}/confirm", headers=headers) for _ in range(3)]
    )
    assert all(r.status_code == 200 and r.json()["status"] == "confirmed" for r in responses)
    assert env.DB.connection.execute("SELECT COUNT(*) FROM cf_outbox").fetchone()[0] == 1


async def test_new_effective_fx_rate_conflicts_and_foreign_references_are_rejected(client, env):
    headers, party, product = await setup(client)
    response = await client.put(
        "/api/currencies/rates",
        headers=headers,
        json={"currency": "EUR", "rate_date": "2026-01-01", "rate": "2"},
    )
    assert response.status_code == 200
    data = {**body(party, product), "order_date": "2026-03-01", "currency": "EUR"}

    async def change_rate(statements):
        statement, _ = tables()["core_fx_rates"].insert(
            {
                "org_id": env.ORG_ID,
                "currency": "EUR",
                "rate_date": date(2026, 2, 1),
                "rate": Decimal(4),
            }
        )
        await D1Database(env.DB).prepare(statement).run()

    env.DB.before_batch = change_rate
    response = await client.post("/api/sales/orders", headers=headers, json=data)
    assert response.status_code == 409, response.text
    env.DB.before_batch = None
    assert env.DB.connection.execute(f"SELECT COUNT(*) FROM {SALES.table}").fetchone()[0] == 0
    response = await client.post("/api/sales/orders", headers=headers, json=data)
    assert response.status_code == 201
    row = await Repository(D1Database(env.DB), SALES.table, env.ORG_ID).get(response.json()["id"])
    assert row["total_base"] == Decimal("5.00") and row["number"].endswith("0001")
    other = UnitOfWork(
        D1Database(env.DB), str(uuid.uuid7()), str(uuid.uuid7()), "foreign", "test", {}
    )
    foreign_party = other.add(
        "crm_contacts", {"code": "FOREIGN", "name": "Foreign", "is_customer": True}
    )
    foreign_product = other.add("catalog_products", {"sku": "FOREIGN", "name": "Foreign"})
    foreign_tax = other.add(
        "catalog_taxes", {"code": "FOREIGN", "name": "Foreign", "rate_pct": Decimal(1)}
    )
    await other.commit({"created": True})
    cases = [
        body(foreign_party, product),
        body(party, foreign_product),
        {
            "customer_id": party["id"],
            "lines": [{"qty": "1", "unit_price": "1", "tax_id": foreign_tax["id"]}],
        },
    ]
    for data in cases:
        response = await client.post("/api/sales/orders", headers=headers, json=data)
        assert response.status_code == 422, response.text
