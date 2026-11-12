import uuid
from decimal import Decimal

import pytest

from erp_cf.db import D1Database, assertion
from erp_cf.errors import ConflictError, NotFoundError
from erp_cf.store import Repository, UnitOfWork, tables
from tests.fakes import SQLiteD1


@pytest.fixture
def db():
    return D1Database(SQLiteD1())


def command(db, org, key="one"):
    return UnitOfWork(db, org, str(uuid.uuid7()), key, "test", {})


async def product(db, org):
    plan = command(db, org)
    row = plan.add(
        "catalog_products",
        {
            "sku": "A",
            "name": "Exact product",
            "sale_price": Decimal("999999999999.999999"),
        },
    )
    plan.audit("create", "catalog.product", row["id"])
    await plan.commit({"id": row["id"]})
    return row


@pytest.mark.asyncio
async def test_native_repository_roundtrips_extremes_defaults_and_audit_atomically(db):
    org = str(uuid.uuid7())
    row = await product(db, org)
    stored = await Repository(db, "catalog_products", org).get(row["id"])
    assert stored == row
    assert stored["sale_price"] == Decimal("999999999999.999999")
    assert stored["is_sellable"] is True and stored["created_at"].tzinfo is not None
    assert uuid.UUID(stored["id"]).version == 7
    assert await Repository(db, "core_audit_logs", org).count() == 1


@pytest.mark.asyncio
async def test_stale_or_failed_update_rolls_back_business_and_audit(db):
    org = str(uuid.uuid7())
    row = await product(db, org)
    first, stale = command(db, org), command(db, org)
    first.update("catalog_products", row, {"name": "Updated"})
    await first.commit({"ok": True})
    stale.update("catalog_products", row, {"name": "Stale"})
    stale.audit("update", "catalog.product", row["id"])
    with pytest.raises(ConflictError):
        await stale.commit({"ok": True})
    assert (await Repository(db, "catalog_products", org).get(row["id"]))["name"] == "Updated"
    assert await Repository(db, "core_audit_logs", org).count() == 1
    failed = command(db, org)
    current = await failed.repository("catalog_products").get(row["id"])
    failed.update("catalog_products", current, {"name": "Never committed"})
    failed.statements.append(assertion(failed.identity, "0"))
    with pytest.raises(ConflictError):
        await failed.commit({"ok": True})
    assert await Repository(db, "catalog_products", org).get(row["id"]) == current


@pytest.mark.asyncio
async def test_organization_scoping_applies_to_reads_writes_and_owned_lines(db):
    org, other = str(uuid.uuid7()), str(uuid.uuid7())
    row = await product(db, org)
    assert await Repository(db, "catalog_products", other).find() == []
    with pytest.raises(NotFoundError):
        command(db, other).update("catalog_products", row, {"name": "Forbidden"})
    plan = command(db, org)
    invoice = plan.add("invoicing_invoices", {"invoice_type": "ar", "invoice_date": "2026-11-13"})
    line = plan.add(
        "invoicing_invoice_lines",
        {"invoice_id": invoice["id"], "qty": "1.0000", "unit_price": "1.000000"},
    )
    await plan.commit({"id": invoice["id"]})
    assert await Repository(db, "invoicing_invoice_lines", other).find() == []
    assert (await Repository(db, "invoicing_invoice_lines", org).get(line["id"]))["qty"] == Decimal(
        1
    )
    illegal = command(db, other)
    illegal.add(
        "invoicing_invoice_lines", {"invoice_id": invoice["id"], "qty": "1", "unit_price": "1"}
    )
    with pytest.raises(ConflictError):
        await illegal.commit({"ok": True})


def test_every_decimal_and_integer_projection_uses_text():
    assert len(tables()) == 45
    for table in tables().values():
        projection = table.projection()
        for col in table.columns:
            if col.kind in {"decimal", "int", "bool"}:
                assert f'CAST("{col.name}" AS TEXT)' in projection
