"""Exact report arithmetic, bounded reads and concurrent business changes."""

import sqlite3
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from erp_cf.consistent_reads import ConsistentReader
from erp_cf.db import D1Database, assertion
from erp_cf.documents import today
from erp_cf.errors import ConflictError, DomainError
from erp_cf.reporting import Reports, converted
from tests.test_auth import login
from tests.test_inventory import setup
from tests.test_invoicing import call, invoice, payment, plan, rate


async def report(client, headers, name, **params):
    response = await client.get("/api/reports/" + name, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_empty_reports_keep_shapes_and_dashboard_ends_in_current_week(client):
    headers = {"Authorization": "Bearer " + await login(client)}
    dashboard = await report(client, headers, "dashboard")
    assert all(
        dashboard[key] == "0.00" for key in ["sales_mtd", "purchases_mtd", "open_ar", "open_ap"]
    )
    assert dashboard["low_stock_count"] == 0
    assert len(dashboard["weeks"]) == 13
    monday = today() - timedelta(days=today().weekday())
    assert dashboard["weeks"][0]["week_start"] == (monday - timedelta(weeks=12)).isoformat()
    assert dashboard["weeks"][-1]["week_start"] == monday.isoformat()
    for name in ["sales-by-customer", "purchases-by-supplier", "stock-valuation", "tax-summary"]:
        assert await report(client, headers, name) == []
    assert await report(client, headers, "aging") == [
        {"bucket": name, "amount": "0.00"} for name in ["current", "31-60", "61-90", "90+"]
    ]


async def test_reports_net_credit_snapshots_fx_dates_and_exclude_drafts_and_voids(client):
    headers, _, _, party = await setup(client)
    response = await client.post(
        "/api/catalog/taxes", headers=headers, json={"code": "VAT", "name": "VAT", "rate_pct": "10"}
    )
    assert response.status_code == 201
    tax = response.json()
    await rate(client, headers, "2")

    async def taxed(amount, **fields):
        return await invoice(
            client,
            headers,
            party,
            currency="EUR",
            lines=[{"description": "Taxed", "qty": "1", "unit_price": amount, "tax_id": tax["id"]}],
            **fields,
        )

    ar = await taxed("100")
    await taxed("80", invoice_type="ap")
    await taxed("20", invoice_type="ar_credit", original_invoice_id=ar["id"])
    await taxed("99", post=False)
    voided = await taxed("99")
    await call(client, headers, "POST", f"invoices/{voided['id']}/void")
    old = await invoice(
        client, headers, party, "50", invoice_date="2020-01-01", due_date="2020-01-01"
    )
    await payment(client, headers, party, "22", target=ar)
    dashboard = await report(client, headers, "dashboard")
    assert dashboard["sales_mtd"] == dashboard["purchases_mtd"] == "44.00"
    assert dashboard["open_ar"] == "83.00" and dashboard["open_ap"] == "44.00"
    assert dashboard["low_stock_count"] == 1
    sales = await report(client, headers, "sales-by-customer")
    assert sales[0]["total"] == "94.00" and sales[0]["invoice_count"] == 2
    dated = await report(
        client,
        headers,
        "sales-by-customer",
        date_from=today().isoformat(),
        date_to=today().isoformat(),
    )
    assert dated[0]["total"] == "44.00" and dated[0]["invoice_count"] == 1
    assert (await report(client, headers, "purchases-by-supplier"))[0]["total"] == "44.00"
    aging = {row["bucket"]: row["amount"] for row in await report(client, headers, "aging")}
    assert aging == {"current": "33.00", "31-60": "0.00", "61-90": "0.00", "90+": "50.00"}
    summary = await report(client, headers, "tax-summary")
    taxed_rows = [row for row in summary if row["tax_code"] == "VAT"]
    assert [(row["direction"], row["net"], row["tax"]) for row in taxed_rows] == [
        ("output", "40.00", "4.00"),
        ("input", "40.00", "4.00"),
    ]
    # Updating the master tax must not merge/reprice historical line snapshots.
    response = await client.patch(
        f"/api/catalog/taxes/{tax['id']}",
        headers=headers,
        json={"code": "VAT", "name": "VAT", "rate_pct": "20"},
    )
    assert response.status_code == 200, response.text
    await taxed("30")
    rows = await report(client, headers, "tax-summary")
    assert sorted(
        (row["rate_pct"], row["tax"])
        for row in rows
        if row["direction"] == "output" and row["tax_code"] == "VAT"
    ) == [("10.00", "4.00"), ("20.00", "3.00")]
    await call(client, headers, "POST", f"invoices/{old['id']}/void")


async def test_aging_and_dashboard_use_actual_base_clearing_residual(client):
    headers, _, _, party = await setup(client)
    await rate(client, headers, "3")
    row = await invoice(client, headers, party, "0.05", currency="EUR")
    await payment(client, headers, party, "0.01", target=row)
    # The partial payment clears zero base cents. Re-dividing the remaining
    # document amount (0.04/3) would incorrectly report only 0.01 instead of 0.02.
    assert (await report(client, headers, "dashboard"))["open_ar"] == "0.02"
    assert (await report(client, headers, "aging"))[0]["amount"] == "0.02"


async def test_financial_aggregates_exceed_int64_without_sql_sum_or_float(client, env, monkeypatch):
    headers, _, _, party = await setup(client)
    for _ in range(11):
        await invoice(
            client,
            headers,
            party,
            lines=[{"description": "Extreme", "qty": "10000", "unit_price": "999999999999.999999"}],
        )
    monkeypatch.setattr(ConsistentReader, "page_size", 3)
    with pytest.raises(sqlite3.OperationalError, match="integer overflow"):
        env.DB.connection.execute("SELECT SUM(total_base) FROM invoicing_invoices").fetchone()
    expected = "109999999999999999.89"
    assert (await report(client, headers, "sales-by-customer"))[0]["total"] == expected
    assert (await report(client, headers, "dashboard"))["sales_mtd"] == expected
    assert (await report(client, headers, "aging"))[0]["amount"] == expected
    assert (await report(client, headers, "tax-summary"))[0]["net"] == expected


async def test_tax_conversion_rounds_once_after_summing_lines_and_rates(client):
    headers, _, _, party = await setup(client)
    await rate(client, headers, "2")
    await invoice(
        client,
        headers,
        party,
        currency="EUR",
        lines=[{"description": "Half a cent", "qty": "1", "unit_price": "0.01"} for _ in range(2)],
    )
    assert (await report(client, headers, "tax-summary"))[0]["net"] == "0.01"
    assert converted({200000000: 1}) == Decimal("0.00")
    assert converted({200000000: 3}) == Decimal("0.02")
    assert converted({200000000: -3}) == Decimal("-0.02")
    assert converted({200000000: 1, 600000000: 3}) == Decimal("0.01")


async def test_stock_valuation_aggregates_exact_products_before_rounding(client, env):
    headers, warehouse, product, _ = await setup(client)
    write = plan(env, str(uuid.uuid4()))
    # Two values of 0.12345 round together to 0.25, not separately to 0.24.
    other = write.add("catalog_products", {"sku": "OTHER", "name": "Other"})
    for item in [product, other]:
        write.add(
            "inventory_stock",
            {
                "product_id": item["id"],
                "warehouse_id": warehouse["id"],
                "qty_on_hand": "0.0001",
                "avg_cost": "1234.500000",
            },
        )
    await write.commit({})
    rows = await report(client, headers, "stock-valuation")
    assert (
        rows[0]["products"] == 2
        and rows[0]["qty_on_hand"] == "0.0002"
        and rows[0]["value"] == "0.25"
    )


async def test_paged_open_invoice_does_not_duplicate_or_drop_settlement_effects(
    client, monkeypatch
):
    headers, _, _, party = await setup(client)
    original = await invoice(client, headers, party, "1")
    for _ in range(7):
        await payment(client, headers, party, "0.01", target=original)
    await invoice(client, headers, party, "2")
    monkeypatch.setattr(ConsistentReader, "page_size", 3)
    assert (await report(client, headers, "aging"))[0]["amount"] == "2.93"
    assert (await report(client, headers, "dashboard"))["open_ar"] == "2.93"


async def test_mid_page_void_retries_to_one_complete_committed_version(client, env, monkeypatch):
    headers, _, _, party = await setup(client)
    first = await invoice(client, headers, party, "10")
    await invoice(client, headers, party, "20")
    monkeypatch.setattr(ConsistentReader, "page_size", 1)
    original_all, changed = D1Database.all, False

    async def read(db, sql, params=()):
        nonlocal changed
        rows = await original_all(db, sql, params)
        if not changed and "AS _cursor0" in sql and "FROM invoicing_invoices i WHERE" in sql:
            changed = True
            await call(client, headers, "POST", f"invoices/{first['id']}/void")
        return rows

    monkeypatch.setattr(D1Database, "all", read)
    rows = await report(client, headers, "sales-by-customer")
    assert changed and rows[0]["total"] == "20.00" and rows[0]["invoice_count"] == 1


async def test_mid_page_final_payment_cannot_leave_stale_aging_balance(client, monkeypatch):
    headers, _, _, party = await setup(client)
    original = await invoice(client, headers, party, "1")
    for _ in range(7):
        await payment(client, headers, party, "0.01", target=original)
    monkeypatch.setattr(ConsistentReader, "page_size", 3)
    original_all, changed = D1Database.all, False

    async def read(db, sql, params=()):
        nonlocal changed
        rows = await original_all(db, sql, params)
        if not changed and "AS _cursor0" in sql and "LEFT JOIN cf_settlement_effects e" in sql:
            changed = True
            await payment(client, headers, party, "0.93", target=original)
        return rows

    monkeypatch.setattr(D1Database, "all", read)
    rows = await report(client, headers, "aging")
    assert changed and all(row["amount"] == "0.00" for row in rows)


async def test_epochs_advance_once_on_commit_and_not_on_rollback_or_replay(client, env):
    await login(client)
    reader = Reports(D1Database(env.DB), env.ORG_ID)
    before = int(await reader.epoch())
    write = plan(env, "epoch-once")
    write.add("catalog_products", {"sku": "EPOCH", "name": "Epoch test"})
    await write.commit({"created": True})
    assert int(await reader.epoch()) == before + 1
    await write.commit({"created": True})
    assert int(await reader.epoch()) == before + 1
    failed = plan(env, "epoch-fail")
    failed.add("catalog_products", {"sku": "NEVER", "name": "Never"})
    failed.statements.append(assertion(failed.identity, "0"))
    with pytest.raises(ConflictError):
        await failed.commit({})
    assert int(await reader.epoch()) == before + 1
    calls = 0

    async def always_changing():
        nonlocal calls
        calls += 1
        await plan(env, str(uuid.uuid4())).commit({})
        return "discarded"

    with pytest.raises(DomainError) as error:
        await reader.run(always_changing)
    assert error.value.status == 503 and error.value.code == "report_busy" and calls == 3


async def test_epoch_rolls_back_after_increment_and_survives_lost_commit_response(env):
    reader = Reports(D1Database(env.DB), env.ORG_ID)
    before = await reader.epoch()
    failed = plan(env, "epoch-late-fault")
    failed.add("catalog_products", {"sku": "LATE", "name": "Rollback"})

    async def fail_after_epoch(statements):
        statements.append(
            env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)")
        )

    env.DB.before_batch = fail_after_epoch
    with pytest.raises(ConflictError):
        await failed.commit({})
    env.DB.before_batch = None
    assert await reader.epoch() == before
    successful = plan(env, "epoch-lost-response")
    successful.add("catalog_products", {"sku": "LOST", "name": "Lost response"})
    original_batch = env.DB.batch

    async def lose_response(statements):
        await original_batch(statements)
        env.DB.batch = original_batch
        raise ConnectionError("Commit response lost")

    env.DB.batch = lose_response
    assert await successful.commit({"ok": True}) == {"ok": True}
    assert int(await reader.epoch()) == int(before) + 1
    assert await successful.commit({"ok": True}) == {"ok": True}
    assert int(await reader.epoch()) == int(before) + 1


async def test_report_scope_excludes_other_organizations_and_requires_permission(client, env):
    headers, _, _, _ = await setup(client)
    foreign = plan(env, "foreign-report")
    foreign.org_id = str(uuid.uuid7())
    foreign.add(
        "invoicing_invoices",
        {
            "invoice_type": "ar",
            "invoice_date": today(),
            "status": "posted",
            "total": "500",
            "total_base": "500",
        },
    )
    await foreign.commit({})
    assert await report(client, headers, "sales-by-customer") == []
    assert (await report(client, headers, "dashboard"))["open_ar"] == "0.00"
    role = await client.post(
        "/api/roles",
        headers=headers,
        json={"code": "leave", "name": "Leave only", "permission_codes": ["hr.leave.request"]},
    )
    assert role.status_code == 201
    user = await client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "leave@example.com",
            "password": "password123",
            "full_name": "Leave",
            "role_codes": ["leave"],
        },
    )
    assert user.status_code == 201
    response = await client.post(
        "/api/auth/login", json={"email": "leave@example.com", "password": "password123"}
    )
    forbidden = {"Authorization": "Bearer " + response.json()["access_token"]}
    for name in [
        "dashboard",
        "sales-by-customer",
        "purchases-by-supplier",
        "aging",
        "stock-valuation",
        "tax-summary",
    ]:
        response = await client.get("/api/reports/" + name, headers=forbidden)
        assert response.status_code == 403, response.text
        assert (await client.get("/api/reports/" + name)).status_code == 401


async def test_low_stock_count_includes_unstocked_pairs_without_cartesian_scan(client, env):
    headers, warehouse, product, _ = await setup(client)
    write = plan(env, "low-stock")
    other_warehouse = write.add("inventory_warehouses", {"code": "SECOND", "name": "Second"})
    other = write.add("catalog_products", {"sku": "TWO", "name": "Two", "min_stock": "3"})
    for index, fields in enumerate(
        [{"status": "archived"}, {"type": "service"}, {"track_inventory": False}]
    ):
        write.add(
            "catalog_products",
            {"sku": f"IGNORE-{index}", "name": "Excluded", "min_stock": "100", **fields},
        )
    for item, place, quantity in [(product, warehouse, "6"), (other, other_warehouse, "3")]:
        write.add(
            "inventory_stock",
            {
                "product_id": item["id"],
                "warehouse_id": place["id"],
                "qty_on_hand": quantity,
                "avg_cost": "1",
            },
        )
    await write.commit({})
    assert (await report(client, headers, "dashboard"))["low_stock_count"] == 3


async def test_complete_remote_report_verifier_against_native_binding(env, tmp_path, monkeypatch):
    import json

    import httpx

    from erp_cf.app import create_app
    from scripts import verify_reporting_remote

    credentials = tmp_path / ".demo.app-staging.json"
    credentials.write_text(json.dumps({"email": "admin@example.com", "password": "admin123"}))
    credentials.chmod(0o600)
    monkeypatch.setattr(verify_reporting_remote, "ROOT", tmp_path)
    native_client, application = httpx.AsyncClient, create_app(env)

    def client(**kwargs):
        return native_client(**kwargs, transport=httpx.ASGITransport(app=application))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    await verify_reporting_remote.main()


async def test_staging_interleaving_proof_is_guarded_and_discards_the_partial_page(client, env):
    headers, _, _, party = await setup(client)
    row = await invoice(
        client,
        headers,
        party,
        lines=[{"description": f"Proof {i}", "qty": "1", "unit_price": "0.01"} for i in range(501)],
    )
    path, body = "/internal/proofs/reports/void-after-first-page", {"invoice_id": row["id"]}
    assert (await client.post(path, headers=headers, json=body)).status_code == 403
    proof_headers = {**headers, "x-bootstrap-token": env.BOOTSTRAP_TOKEN}
    env.ENVIRONMENT = "production"
    assert (await client.post(path, headers=proof_headers, json=body)).status_code == 404
    env.ENVIRONMENT = "staging"
    assert (
        await client.post(path, headers={"x-bootstrap-token": env.BOOTSTRAP_TOKEN}, json=body)
    ).status_code == 401
    response = await client.post(
        "/api/roles",
        headers=headers,
        json={
            "code": "proof",
            "name": "Proof permissions",
            "permission_codes": ["reports.view", "invoicing.invoice.void"],
        },
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "proof@example.com",
            "password": "password123",
            "full_name": "Non administrator",
            "role_codes": ["proof"],
        },
    )
    assert response.status_code == 201
    token = (
        await client.post(
            "/api/auth/login", json={"email": "proof@example.com", "password": "password123"}
        )
    ).json()["access_token"]
    response = await client.post(
        path,
        headers={"Authorization": "Bearer " + token, "x-bootstrap-token": env.BOOTSTRAP_TOKEN},
        json=body,
    )
    assert response.status_code == 403
    assert (await call(client, headers, "GET", f"invoices/{row['id']}"))["status"] == "posted"
    result = await client.post(path, headers=proof_headers, json=body)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["changed"] and result["page_queries"] >= 3
    assert int(result["after_epoch"]) > int(result["before_epoch"])
    assert result["rows"] == []
    assert (await call(client, headers, "GET", f"invoices/{row['id']}"))["status"] == "void"
