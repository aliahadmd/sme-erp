"""Workers AI budgets, review contracts, stale sources and durable bulk jobs."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from erp_cf.ai_budget import DailyAIBudget
from erp_cf.db import D1Database
from erp_cf.store import UnitOfWork
from tests.ai_fakes import Storage, enable
from tests.jobs_fakes import Message, deliver, drain
from tests.jobs_fakes import enable as enable_jobs
from tests.test_auth import login
from tests.test_background import rows


async def setup(client, count=1):
    headers = {"Authorization": "Bearer " + await login(client)}
    products = []
    for n in range(count):
        response = await client.post(
            "/api/catalog/products",
            headers=headers,
            json={"name": f"AI Product {n}", "sale_price": "1"},
        )
        assert response.status_code == 201, response.text
        products.append(response.json())
    return headers, products


async def drain_all(env):
    messages = []
    for _ in range(20):
        current = await drain(env)
        messages.extend(current)
        if not env.EVENTS.messages:
            return messages
    raise AssertionError("AI outbox did not settle")


def used(env):
    budget = env.AI_BUDGETS.getByName(env.ORG_ID)
    result = budget.storage.sql.exec("SELECT used FROM ai_budget").toArray()
    return result[0]["used"] if result else 0


async def test_disabled_ai_preserves_contract_and_does_not_charge_budget(client, env):
    provider = enable(env)
    env.AI_ENABLED = "false"
    headers, products = await setup(client)
    calls = [
        ("/api/ai/echo", {"prompt": "hello"}),
        ("/api/ai/summarize", {"report": "sales", "payload": {"total": "12.00"}}),
        ("/api/catalog/products/generate-missing", None),
        (f"/api/catalog/products/{products[0]['id']}/generate-description", None),
    ]
    for path, body in calls:
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code == 503 and response.json()["error"]["code"] == "ai_disabled"
    assert not provider.calls and used(env) == 0
    assert not rows(env, "SELECT * FROM cf_outbox")
    assert (await client.get("/api/ai/drafts", headers=headers)).json() == []
    assert (await client.get("/api/ai/search?q=product", headers=headers)).json()[0][
        "id"
    ] == products[0]["id"]


async def test_shared_daily_quota_covers_concurrent_echo_summary_and_single_product(client, env):
    provider = enable(env, limit=2)
    headers, products = await setup(client)
    requests = [
        client.post("/api/ai/echo", headers=headers, json={"prompt": "Hello"}),
        client.post(
            "/api/ai/summarize",
            headers=headers,
            json={"report": "sales", "payload": {"total": "12.00"}},
        ),
        client.post(
            f"/api/catalog/products/{products[0]['id']}/generate-description", headers=headers
        ),
    ]
    responses = await asyncio.gather(*requests)
    assert sorted(r.status_code for r in responses) == [200, 200, 429]
    assert len(provider.calls) == used(env) == 2
    # Evict/recreate the counter object while keeping its SQLite storage.
    env.AI_BUDGETS.instances.clear()
    response = await client.post("/api/ai/echo", headers=headers, json={"prompt": "after restart"})
    assert response.status_code == 429 and len(provider.calls) == 2
    assert rows(env, "SELECT description FROM catalog_products") == [{"description": None}]


async def test_provider_errors_empty_output_and_oversized_input_are_explicit(client, env, caplog):
    provider = enable(env)
    headers, _ = await setup(client)
    private = "PRIVATE-PROMPT-CONTENT"
    provider.error = RuntimeError(private)
    response = await client.post("/api/ai/echo", headers=headers, json={"prompt": private})
    assert response.status_code == 502 and response.json()["error"]["code"] == "ai_error"
    assert private not in response.text and private not in caplog.text
    provider.error, provider.output = None, {"response": ""}
    assert (
        await client.post("/api/ai/echo", headers=headers, json={"prompt": "x"})
    ).status_code == 502
    before = used(env)
    response = await client.post(
        "/api/ai/summarize",
        headers=headers,
        json={"report": "sales", "payload": {"data": "X" * 33000}},
    )
    assert response.status_code == 422 and used(env) == before == 2


async def test_daily_quota_alarm_does_not_reset_current_day_and_expires_old_day(env):
    env.AI_DAILY_REQUEST_LIMIT = "2"
    storage, current = Storage(), [datetime(2026, 11, 13, tzinfo=UTC)]
    first = DailyAIBudget(storage, env, clock=lambda: current[0])
    assert (await first.reserve())["allowed"]
    await first.expire()  # Early or duplicate alarm.
    assert (await first.reserve())["allowed"]
    restarted = DailyAIBudget(storage, env, clock=lambda: current[0])
    assert not (await restarted.reserve())["allowed"]
    current[0] += timedelta(days=1)
    await restarted.expire()
    assert storage.sql.exec("SELECT * FROM ai_budget").toArray() == [] and storage.alarm is None
    assert (await restarted.reserve())["used"] == 1


async def test_bulk_generation_pages_and_creates_only_reviewable_drafts_once(client, env):
    enable_jobs(env)
    provider = enable(env)
    headers, products = await setup(client, 26)
    key = "bulk-once"
    first = await client.post(
        "/api/catalog/products/generate-missing", headers={**headers, "Idempotency-Key": key}
    )
    repeat = await client.post(
        "/api/catalog/products/generate-missing", headers={**headers, "Idempotency-Key": key}
    )
    assert first.json() == repeat.json() == {"queued": 26}
    # A distinct, overlapping request must not generate another pending draft.
    assert (
        await client.post("/api/catalog/products/generate-missing", headers=headers)
    ).status_code == 200
    messages = await drain_all(env)
    drafts = (await client.get("/api/ai/drafts", headers=headers)).json()
    assert len(drafts) == len(provider.calls) == used(env) == 26
    assert {row["entity_id"] for row in drafts} == {p["id"] for p in products}
    assert (
        rows(env, "SELECT COUNT(*) AS n FROM catalog_products WHERE description IS NOT NULL")[0][
            "n"
        ]
        == 0
    )
    assert rows(env, "SELECT COUNT(*) AS n FROM cf_ai_tasks WHERE status='pending'")[0]["n"] == 0
    await deliver(env, *[Message(m.body) for m in messages])
    assert len(provider.calls) == 26 and len(rows(env, "SELECT * FROM core_ai_drafts")) == 26
    target = drafts[0]
    accepted = await client.post(
        f"/api/ai/drafts/{target['id']}/accept",
        headers={**headers, "Idempotency-Key": "accept-once"},
    )
    assert accepted.status_code == 200 and accepted.json() == {"status": "accepted"}
    assert (
        await client.post(
            f"/api/ai/drafts/{target['id']}/accept",
            headers={**headers, "Idempotency-Key": "accept-once"},
        )
    ).json() == accepted.json()
    assert (
        await client.post(f"/api/ai/drafts/{target['id']}/discard", headers=headers)
    ).status_code == 409
    product = await client.get("/api/catalog/products/" + target["entity_id"], headers=headers)
    assert product.json()["description"] == target["draft_text"]
    discarded = await client.post(f"/api/ai/drafts/{drafts[1]['id']}/discard", headers=headers)
    assert discarded.status_code == 200 and discarded.json() == {"status": "discarded"}
    assert len((await client.get("/api/ai/drafts", headers=headers)).json()) == 24


async def test_product_changed_during_inference_skips_stale_output_without_holding_lock(
    client, env
):
    enable_jobs(env)
    provider = enable(env)
    headers, products = await setup(client)
    await client.post("/api/catalog/products/generate-missing", headers=headers)

    async def edit():
        response = await client.patch(
            "/api/catalog/products/" + products[0]["id"],
            headers=headers,
            json={"description": "Human description"},
        )
        assert response.status_code == 200, response.text

    provider.before_result = edit
    await drain_all(env)
    assert len(provider.calls) == 1
    assert rows(env, "SELECT status FROM cf_ai_tasks") == [{"status": "skipped"}]
    assert not rows(env, "SELECT * FROM core_ai_drafts")
    assert rows(env, "SELECT description FROM catalog_products") == [
        {"description": "Human description"}
    ]


async def test_model_failure_retries_and_failed_draft_batch_is_atomic(client, env):
    enable_jobs(env)
    provider = enable(env)
    headers, _ = await setup(client)
    await client.post("/api/catalog/products/generate-missing", headers=headers)
    await drain(env)  # Batch planner queues the individual product task.
    raw = env.EVENTS.messages.pop()["body"]
    original = env.DB.before_batch

    async def fail_receipt(statements):
        if any("INSERT INTO cf_event_receipts" in s.sql for s in statements):
            env.DB.connection.execute(
                "CREATE TEMP TRIGGER fail_ai_receipt BEFORE INSERT ON cf_event_receipts "
                "BEGIN SELECT RAISE(ABORT,'injected ai fault'); END"
            )

    env.DB.before_batch = fail_receipt
    failed = Message(raw)
    await deliver(env, failed)
    assert failed.retry_options and not failed.acknowledged
    assert not rows(env, "SELECT * FROM core_ai_drafts")
    assert rows(env, "SELECT status FROM cf_ai_tasks") == [{"status": "pending"}]
    env.DB.connection.execute("DROP TRIGGER fail_ai_receipt")
    env.DB.before_batch = original
    retry = Message(raw, attempts=2)
    await deliver(env, retry)
    assert retry.acknowledged
    assert len(provider.calls) == used(env) == 2  # A real repeated provider attempt is charged.
    assert len(rows(env, "SELECT * FROM core_ai_drafts")) == 1


async def test_bulk_quota_defers_in_d1_without_exhausting_queue_retries(client, env):
    enable_jobs(env)
    provider = enable(env, limit=1)
    headers, _ = await setup(client, 2)
    await client.post("/api/catalog/products/generate-missing", headers=headers)
    await drain_all(env)
    pending = rows(env, "SELECT id,available_at FROM cf_outbox WHERE completed_at IS NULL")
    assert len(pending) == 1 and pending[0]["available_at"] > datetime.now(UTC).isoformat()
    assert not rows(env, "SELECT * FROM cf_dead_letters")
    assert len(provider.calls) == used(env) == 1
    assert (
        await client.post("/api/catalog/products/generate-missing", headers=headers)
    ).json() == {"queued": 0}
    budget = env.AI_BUDGETS.getByName(env.ORG_ID)
    budget.clock = lambda: datetime.now(UTC) + timedelta(days=1)
    env.DB.connection.execute(
        "UPDATE cf_outbox SET available_at='2000-01-01' WHERE id=?", (pending[0]["id"],)
    )
    await drain_all(env)
    assert len(provider.calls) == 2 and used(env) == 1
    assert len(rows(env, "SELECT * FROM core_ai_drafts")) == 2
    assert not rows(env, "SELECT id FROM cf_outbox WHERE completed_at IS NULL")


async def test_search_unicode_and_review_field_ownership_are_preserved(client, env):
    headers, products = await setup(client)
    response = await client.patch(
        "/api/catalog/products/" + products[0]["id"],
        headers=headers,
        json={"description": "Straße 铜管"},
    )
    assert response.status_code == 200
    response = await client.get("/api/ai/search", headers=headers, params={"q": "STRASSE 铜管"})
    assert response.status_code == 200 and response.json()[0]["id"] == products[0]["id"]
    actor = rows(env, "SELECT id FROM core_users")[0]["id"]
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, actor, uuid.uuid4().hex, "test", {})
    draft = plan.add(
        "core_ai_drafts",
        {
            "entity_type": "product",
            "entity_id": products[0]["id"],
            "field": "sale_price",
            "draft_text": "999",
            "model": "test",
        },
    )
    await plan.commit({})
    response = await client.post(f"/api/ai/drafts/{draft['id']}/accept", headers=headers)
    assert response.status_code == 404
    env.DB.connection.execute(
        "UPDATE core_ai_drafts SET org_id=? WHERE id=?", (str(uuid.uuid4()), draft["id"])
    )
    assert (
        await client.post(f"/api/ai/drafts/{draft['id']}/discard", headers=headers)
    ).status_code == 404


async def test_inference_and_review_permissions_are_checked_before_any_budget(client, env):
    provider = enable(env)
    headers, _ = await setup(client)
    created = await client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "ai-unauthorized@example.com",
            "full_name": "No AI permissions",
            "password": "test-ai-password",
            "role_codes": [],
        },
    )
    assert created.status_code == 201
    login_response = await client.post(
        "/api/auth/login",
        json={
            "email": "ai-unauthorized@example.com",
            "password": "test-ai-password",
        },
    )
    auth = {"Authorization": "Bearer " + login_response.json()["access_token"]}
    for path, body in [
        ("/api/ai/echo", {"prompt": "hello"}),
        ("/api/ai/summarize", {"report": "sales", "payload": {}}),
        ("/api/catalog/products/generate-missing", None),
    ]:
        assert (await client.post(path, headers=auth, json=body)).status_code == 403
    assert (await client.get("/api/ai/drafts", headers=auth)).status_code == 403
    assert (await client.get("/api/ai/search?q=anything", headers=auth)).status_code == 403
    assert not provider.calls and used(env) == 0


@pytest.mark.parametrize("when", ["before_planner", "during_inference"])
async def test_bulk_requester_revocation_is_rechecked_before_planning_and_commit(client, env, when):
    enable_jobs(env)
    provider = enable(env)
    headers, _ = await setup(client)
    role = await client.post(
        "/api/roles",
        headers=headers,
        json={
            "code": "ai_writer",
            "name": "AI writer",
            "permission_codes": ["catalog.product.update"],
        },
    )
    assert role.status_code == 201
    user = await client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "ai-writer@example.com",
            "full_name": "AI writer",
            "password": "test-ai-writer",
            "role_codes": ["ai_writer"],
        },
    )
    assert user.status_code == 201
    signed_in = await client.post(
        "/api/auth/login",
        json={
            "email": "ai-writer@example.com",
            "password": "test-ai-writer",
        },
    )
    writer = {"Authorization": "Bearer " + signed_in.json()["access_token"]}
    response = await client.post("/api/catalog/products/generate-missing", headers=writer)
    assert response.status_code == 200

    async def deactivate():
        changed = await client.patch(
            "/api/users/" + user.json()["id"], headers=headers, json={"is_active": False}
        )
        assert changed.status_code == 200

    if when == "before_planner":
        await deactivate()
    else:
        provider.before_result = deactivate
    await drain_all(env)
    assert not rows(env, "SELECT * FROM core_ai_drafts")
    assert len(provider.calls) == (0 if when == "before_planner" else 1)
    assert not rows(env, "SELECT id FROM cf_outbox WHERE completed_at IS NULL")
