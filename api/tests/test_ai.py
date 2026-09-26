"""AI features: disabled-path guards + review queue lifecycle."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.core.models import AiDraft, Organization
from tests.conftest import TEST_DATABASE_URL


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin(client) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return response.json()["access_token"]


async def test_summarize_disabled_without_key(client):
    token = await _admin(client)
    response = await client.post(
        "/api/ai/summarize",
        headers=_auth(token),
        json={"report": "sales", "payload": {"total": 100}},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_disabled"


async def test_generate_missing_disabled_without_key(client):
    token = await _admin(client)
    response = await client.post("/api/catalog/products/generate-missing", headers=_auth(token))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_disabled"


async def test_semantic_search_fallback_without_ai(client):
    token = await _admin(client)
    await client.post(
        "/api/catalog/products",
        headers=_auth(token),
        json={"name": "Copper Pipe", "sale_price": "3.00"},
    )
    results = (
        await client.get("/api/ai/search", headers=_auth(token), params={"q": "copper"})
    ).json()
    assert any(r["title"] == "Copper Pipe" for r in results)


async def test_draft_review_lifecycle(client):
    admin = await _admin(client)
    product = (
        await client.post(
            "/api/catalog/products",
            headers=_auth(admin),
            json={"name": "Draft Widget", "sale_price": "1.00"},
        )
    ).json()

    # Insert a pending draft directly (the worker path is AI-key-gated)
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            org = (await session.scalars(select(Organization).limit(1))).first()
            session.add(
                AiDraft(
                    org_id=org.id,
                    entity_type="product",
                    entity_id=product["id"],
                    field="description",
                    draft_text="A great widget.",
                    model="test",
                )
            )
            await session.commit()
    finally:
        await engine.dispose()

    drafts = (await client.get("/api/ai/drafts", headers=_auth(admin))).json()
    target = next(d for d in drafts if d["entity_id"] == product["id"])

    accepted = await client.post(f"/api/ai/drafts/{target['id']}/accept", headers=_auth(admin))
    assert accepted.status_code == 200
    product_after = (
        await client.get(f"/api/catalog/products/{product['id']}", headers=_auth(admin))
    ).json()
    assert product_after["description"] == "A great widget."


async def test_ai_budget_enforced(client):
    """Without a key each call is ai_disabled(503); the budget guard still
    limits daily usage — third call exceeds the limit of 2."""
    import os
    from datetime import date

    admin = await _admin(client)
    headers = _auth(admin)
    key = f"ai:budget:{date.today().isoformat()}"

    # start from a clean budget counter (redis persists across runs)
    from redis.asyncio import from_url as aioredis_from_url

    from app.core.config import get_settings

    r = aioredis_from_url(get_settings().redis_url, decode_responses=True)
    try:
        await r.delete(key)
    finally:
        await r.aclose()

    os.environ["AI_DAILY_REQUEST_LIMIT"] = "2"
    try:
        statuses = []
        for i in range(3):
            response = await client.post(
                "/api/ai/summarize",
                headers=headers,
                json={"report": "sales", "payload": {"i": i}},
            )
            statuses.append(response.status_code)
        # calls 1-2: ai_disabled(503) — no key configured; call 3: budget(429)
        assert statuses == [503, 503, 429]
    finally:
        os.environ.pop("AI_DAILY_REQUEST_LIMIT", None)
