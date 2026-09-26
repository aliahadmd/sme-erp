"""Every plan lands with tests — these prove the foundation endpoints."""


async def test_healthz_all_dependencies_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"postgres": "ok", "redis": "ok", "s3": "ok"}


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
