import asyncio
import json
import uuid

from app.modules.core.schemas import UserCreateIn

from erp_cf.db import AtomicCommands, D1Database
from erp_cf.numbering import next_number
from erp_cf.store import Repository, UnitOfWork, wire
from tests.test_auth import login


async def auth(client):
    return {"Authorization": "Bearer " + await login(client)}


async def request(client, method, path, headers, body=None, status=200):
    response = await client.request(method, path, headers=headers, json=body)
    assert response.status_code == status, response.text
    return response


async def test_concurrent_duplicate_creates_one_number_contact_and_audit(client, env):
    headers = {**await auth(client), "Idempotency-Key": "one-contact"}
    body = {"name": "Idempotent", "is_customer": True}
    responses = await asyncio.gather(
        *(client.post("/api/crm/contacts", headers=headers, json=body) for _ in range(6))
    )
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    db = D1Database(env.DB)
    assert await Repository(db, "crm_contacts", env.ORG_ID).count() == 1
    assert (
        await Repository(db, "core_audit_logs", env.ORG_ID).count("entity_type='crm.contact'") == 1
    )
    seq = await Repository(db, "core_document_sequences", env.ORG_ID).first("entity='contact'")
    assert seq["last_number"] == 1
    changed = await client.post(
        "/api/crm/contacts", headers=headers, json={**body, "name": "Different"}
    )
    assert changed.status_code == 409
    distinct = await asyncio.gather(
        *(
            client.post(
                "/api/crm/contacts",
                headers={**headers, "Idempotency-Key": f"contact-{i}"},
                json={**body, "name": f"C{i}"},
            )
            for i in range(10)
        )
    )
    assert all(r.status_code == 201 for r in distinct)
    assert len({r.json()["code"] for r in distinct}) == 10


async def test_failure_after_audit_rolls_back_number_record_search_key_and_receipt(client, env):
    headers = {**await auth(client), "Idempotency-Key": "failed-contact"}
    body = {"name": "Never committed", "is_customer": True}

    def reject(statements):
        statements.append(env.DB.prepare("INSERT INTO cf_assertions VALUES ('injected',0)"))

    async def before_batch(statements):
        reject(statements)

    env.DB.before_batch = before_batch
    response = await client.post("/api/crm/contacts", headers=headers, json=body)
    assert response.status_code == 409
    for table in ["crm_contacts", "core_document_sequences", "cf_commands"]:
        assert env.DB.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    env.DB.before_batch = None
    created = await request(client, "POST", "/api/crm/contacts", headers, body, 201)
    assert created.json()["code"].endswith("0001")
    replay = await request(client, "POST", "/api/crm/contacts", headers, body, 201)
    assert replay.json() == created.json()


async def test_password_command_receipt_is_keyed_and_public_records_omit_credentials(client, env):
    headers = {**await auth(client), "Idempotency-Key": "credential-create"}
    profile = (await client.get("/api/auth/me", headers=headers)).json()
    body = {
        "email": "private@example.com",
        "password": "private-password-123",
        "full_name": "Private",
    }
    created = await request(client, "POST", "/api/users", headers, body, 201)
    assert "hashed_password" not in created.json()
    identity = AtomicCommands.identity(env.ORG_ID, profile["id"], "credential-create")
    row = env.DB.connection.execute(
        "SELECT request_hash,response_json FROM cf_commands WHERE id=?", (identity,)
    ).fetchone()
    plain = AtomicCommands.request_hash(
        "core.user.create", {"body": wire(UserCreateIn.model_validate(body))}
    )
    assert row["request_hash"] != plain
    assert body["password"] not in row["response_json"] and "$argon2" not in row["response_json"]
    assert json.loads(row["response_json"])["body"] == created.json()


async def test_native_master_values_and_unicode_search_remain_exact(client):
    headers = await auth(client)
    contact = (
        await request(
            client,
            "POST",
            "/api/crm/contacts",
            headers,
            {
                "name": "HÅNDEL",
                "is_customer": True,
                "tags": ["special"],
                "credit_limit": "9999999999999999.99",
            },
            201,
        )
    ).json()
    assert contact["credit_limit"] == "9999999999999999.99"
    found = await client.get(
        "/api/crm/contacts", headers=headers, params={"q": "hånde", "tag": "special"}
    )
    assert [r["id"] for r in found.json()["items"]] == [contact["id"]]
    product = (
        await request(
            client,
            "POST",
            "/api/catalog/products",
            headers,
            {"name": "Ångström Δevice", "sale_price": "999999999999.999999"},
            201,
        )
    ).json()
    stored = (
        await request(client, "GET", f"/api/catalog/products/{product['id']}", headers)
    ).json()
    assert stored["sale_price"] == "999999999999.999999"
    found = await client.get("/api/catalog/products", headers=headers, params={"q": "ångSTRÖM"})
    assert found.json()["total"] == 1
    rate = (
        await request(
            client,
            "PUT",
            "/api/currencies/rates",
            headers,
            {"currency": "eur", "rate_date": "2026-11-13", "rate": "9999999999.99999999"},
        )
    ).json()
    assert rate["rate"] == "9999999999.99999999"


async def test_partial_updates_cannot_remove_contact_role_or_enable_service_inventory(client):
    headers = await auth(client)
    contact = (
        await request(
            client,
            "POST",
            "/api/crm/contacts",
            headers,
            {"name": "Customer", "is_customer": True},
            201,
        )
    ).json()
    await request(
        client, "PATCH", f"/api/crm/contacts/{contact['id']}", headers, {"is_customer": False}, 422
    )
    product = (
        await request(
            client,
            "POST",
            "/api/catalog/products",
            headers,
            {"name": "Service", "type": "service", "track_inventory": False},
            201,
        )
    ).json()
    await request(
        client,
        "PATCH",
        f"/api/catalog/products/{product['id']}",
        headers,
        {"track_inventory": True},
        422,
    )


async def test_role_revocation_and_password_reset_invalidate_existing_authorization(client):
    admin = await auth(client)
    role = (
        await request(
            client,
            "POST",
            "/api/roles",
            admin,
            {"code": "creator", "name": "Creator", "permission_codes": ["catalog.product.create"]},
            201,
        )
    ).json()
    user = (
        await request(
            client,
            "POST",
            "/api/users",
            admin,
            {
                "email": "creator@example.com",
                "full_name": "Creator",
                "password": "password123",
                "role_codes": ["creator"],
            },
            201,
        )
    ).json()
    token = (
        await client.post(
            "/api/auth/login", json={"email": "creator@example.com", "password": "password123"}
        )
    ).json()["access_token"]
    staff = {"Authorization": "Bearer " + token}
    await request(client, "POST", "/api/catalog/products", staff, {"name": "Allowed"}, 201)
    await request(client, "PATCH", f"/api/roles/{role['id']}", admin, {"permission_codes": []})
    await request(client, "POST", "/api/catalog/products", staff, {"name": "Denied"}, 403)
    await request(
        client, "PATCH", f"/api/users/{user['id']}", admin, {"password": "replacement-password"}
    )
    await request(client, "GET", "/api/auth/me", staff, status=401)
    await request(client, "POST", "/api/auth/refresh", {}, status=401)


async def test_bulk_number_reservation_and_delete_replay(client, env):
    headers = await auth(client)
    db = D1Database(env.DB)
    plan = UnitOfWork(db, env.ORG_ID, str(uuid.uuid7()), "numbers", "test", {})
    first, second = await next_number(plan, "sales_order"), await next_number(plan, "sales_order")
    assert first.endswith("0001") and second.endswith("0002")
    await plan.commit({"ok": True})
    category = (
        await request(
            client, "POST", "/api/catalog/categories", headers, {"name": "Temporary"}, 201
        )
    ).json()
    key = {**headers, "Idempotency-Key": "category-delete"}
    await request(client, "DELETE", f"/api/catalog/categories/{category['id']}", key, status=204)
    await request(client, "DELETE", f"/api/catalog/categories/{category['id']}", key, status=204)


async def test_master_inputs_round_half_even_at_declared_scales(client):
    headers = await auth(client)
    contact = (
        await request(
            client,
            "POST",
            "/api/crm/contacts",
            headers,
            {"name": "Rounded", "is_customer": True, "credit_limit": "1.005"},
            201,
        )
    ).json()
    assert contact["credit_limit"] == "1.00"
    product = (
        await request(
            client,
            "POST",
            "/api/catalog/products",
            headers,
            {"name": "Rounded", "sale_price": "1.0000005", "min_stock": "1.00005"},
            201,
        )
    ).json()
    assert product["sale_price"] == "1.000000" and product["min_stock"] == "1.0000"
    await request(
        client,
        "PUT",
        "/api/currencies/rates",
        headers,
        {"currency": "EUR", "rate_date": "2026-11-13", "rate": "0.000000001"},
        422,
    )
