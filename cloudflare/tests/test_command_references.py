"""Command-local reads stay fresh and retain their atomic revision guards."""

import re
import uuid
from datetime import UTC, datetime

import pytest

from erp_cf.accounting import Ledger
from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.fx import snapshot
from erp_cf.numbering import next_number
from erp_cf.stock import StockBook
from erp_cf.store import UnitOfWork
from tests.test_auth import login


def plan(env, key, db=None):
    return UnitOfWork(db or D1Database(env.DB), env.ORG_ID, None, key, "reference-proof", {})


async def test_related_configuration_and_custom_mapped_accounts_use_one_read_batch(
    env, monkeypatch
):
    setup = plan(env, "custom-mapping")
    custom = setup.add(
        "accounting_accounts", {"code": "1199", "name": "Custom AR", "type": "asset"}
    )
    mapping = await setup.repository("core_settings").first("key='accounting.mapping'")
    setup.update("core_settings", mapping, {"value": {"ar": "1199"}})
    await setup.commit({"id": custom["id"]})
    db = D1Database(env.DB)
    calls = []
    original_all, original_batch = db.all, db.batch

    async def all_rows(*args, **kwargs):
        calls.append("single")
        return await original_all(*args, **kwargs)

    async def batch(statements):
        calls.append("batch")
        return await original_batch(statements)

    monkeypatch.setattr(db, "all", all_rows)
    monkeypatch.setattr(db, "batch", batch)
    current = plan(env, "read-once", db)
    book = Ledger(current)
    assert (await book.account("ar"))["id"] == custom["id"]
    assert (await book.account("bank"))["code"] == "1010"
    org = await current.organization()
    assert (await snapshot(current, org["base_currency"], datetime.now(UTC).date(), 12))[
        "total_base"
    ] == 12
    assert await next_number(current, "journal_entry") != await next_number(
        current, "journal_entry"
    )
    assert calls == ["batch"]


async def test_command_cache_is_fresh_next_command_and_stale_policy_rolls_back(env):
    stale = plan(env, "stale-policy")
    assert not await StockBook(stale).allow_negative()
    changed = plan(env, "change-policy")
    setting = await changed.repository("core_settings").first("key='inventory.guard'")
    changed.update("core_settings", setting, {"value": {"allow_negative": True}})
    await changed.commit({"ok": True})
    assert await StockBook(plan(env, "fresh-policy")).allow_negative()
    product = stale.add("catalog_products", {"sku": "STALE-REFERENCE", "name": "Must roll back"})
    with pytest.raises(ConflictError):
        await stale.commit({"id": product["id"]})
    assert await stale.repository("catalog_products").get(product["id"], required=False) is None
    assert await stale.replay() is None


async def test_invoice_post_reuses_one_guarded_source_order(client, monkeypatch):
    headers = {"Authorization": "Bearer " + await login(client)}

    async def create(path, body):
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 201, response.text
        return response.json()

    party = await create("/api/crm/contacts", {"name": "Source proof", "is_customer": True})
    order = await create(
        "/api/sales/orders",
        {
            "customer_id": party["id"],
            "lines": [{"description": "Service", "qty": "1", "unit_price": "60"}],
        },
    )
    assert (
        await client.post(f"/api/sales/orders/{order['id']}/confirm", headers=headers)
    ).status_code == 200
    invoice = await create(
        "/api/invoicing/invoices",
        {
            "party_id": party["id"],
            "source_order_id": order["id"],
        },
    )
    reads = []
    original, original_batch = D1Database.all, D1Database.batch

    def remember(sql):
        if sql.lstrip().upper().startswith("SELECT"):
            table = re.search(r'FROM "([a-z_]+)"', sql)
            if table:
                reads.append(table.group(1))

    async def record(self, sql, params=()):
        remember(sql)
        return await original(self, sql, params)

    async def record_batch(self, statements):
        for statement in statements:
            remember(statement.sql)
        return await original_batch(self, statements)

    monkeypatch.setattr(D1Database, "all", record)
    monkeypatch.setattr(D1Database, "batch", record_batch)
    response = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/post",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 200 and response.json()["total"] == "60.00"
    assert reads.count("sales_sales_orders") == reads.count("sales_sales_order_lines") == 1
