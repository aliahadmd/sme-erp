"""Every plan lands with tests — these prove the foundation endpoints."""


async def test_healthz_reports_dependencies(client):
    response = await client.get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["redis"] == "ok"
    # S3 only backs backups: when it is unreachable (e.g. CI has no S3
    # service) health degrades but stays 200.
    assert body["checks"]["s3"] in ("ok", "down")
    assert body["status"] == ("ok" if body["checks"]["s3"] == "ok" else "degraded")


async def test_meta(client):
    response = await client.get("/api/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["name"]
    assert body["environment"]


async def _login(client):
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_ai_echo_requires_superuser(client):
    response = await client.post("/api/ai/echo", json={"prompt": "hello"})
    assert response.status_code == 401


async def test_ai_echo_disabled_without_key(client):
    headers = await _login(client)
    response = await client.post("/api/ai/echo", headers=headers, json={"prompt": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_disabled"


async def test_validation_error_shape(client):
    headers = await _login(client)
    response = await client.post("/api/ai/echo", headers=headers, json={"prompt": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
