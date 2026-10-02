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


async def test_ai_budget_enforced(client, monkeypatch):
    """With AI enabled (model stubbed) the daily budget allows exactly
    AI_DAILY_REQUEST_LIMIT calls; a disabled AI never consumes budget."""
    from datetime import date

    from redis.asyncio import from_url as aioredis_from_url

    from app.core.ai import AIClient, AIResult
    from app.core.config import get_settings

    admin = await _admin(client)
    headers = _auth(admin)
    key = f"ai:budget:{date.today().isoformat()}"
    settings = get_settings()

    async def _reset_budget() -> None:
        r = aioredis_from_url(settings.redis_url, decode_responses=True)
        try:
            await r.delete(key)
        finally:
            await r.aclose()

    async def _summarize(i: int) -> int:
        response = await client.post(
            "/api/ai/summarize", headers=headers, json={"report": "sales", "payload": {"i": i}}
        )
        return response.status_code

    await _reset_budget()
    # Disabled AI: 503 and no budget consumed.
    assert [await _summarize(i) for i in range(3)] == [503, 503, 503]

    async def fake_complete(self, system: str, prompt: str) -> AIResult:  # noqa: ARG001
        return AIResult(text="- sales are up", model="stub")

    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_daily_request_limit", 2)
    monkeypatch.setattr(AIClient, "complete", fake_complete)
    try:
        assert [await _summarize(i) for i in range(3)] == [200, 200, 429]
    finally:
        await _reset_budget()
