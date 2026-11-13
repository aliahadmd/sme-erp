import asyncio
import copy
import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from erp_cf.app import create_app
from erp_cf.bootstrap import BootstrapInput, seed
from erp_cf.command_bus import Coordinator
from erp_cf.crypto import token_hash
from erp_cf.login_budget import LoginBudget
from tests.fakes import SQLiteD1


class Storage:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        await asyncio.sleep(0)
        return copy.deepcopy(self.data.get(key))

    async def put(self, key, value):
        await asyncio.sleep(0)
        self.data[key] = copy.deepcopy(value)


class Limiters:
    def __init__(self):
        self.objects = {}

    def getByName(self, name):
        if name not in self.objects:
            self.objects[name] = LoginBudget(Storage())
        return self.objects[name]


class Commands:
    def __init__(self, env):
        self.coordinator = Coordinator(env)

    def getByName(self, name):
        return self.coordinator


@pytest_asyncio.fixture
async def env():
    environment = SimpleNamespace(
        DB=SQLiteD1(),
        JWT_SECRET="test-secret-for-tests-only-" * 3,
        COMMAND_HASH_KEY="test-command-fingerprint-key-" * 3,
        PUBLIC_ORIGIN="https://erp.test",
        ORG_ID=str(uuid.uuid7()),
        ENVIRONMENT="staging",
        AUTH_LIMITERS=Limiters(),
        BOOTSTRAP_ENABLED="true",
        BOOTSTRAP_TOKEN="test-bootstrap-token",
    )
    environment.COMMANDS = Commands(environment)
    await seed(
        environment,
        BootstrapInput(email="admin@example.com", password="admin123", full_name="Admin"),
    )
    return environment


@pytest_asyncio.fixture
async def client(env):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(env)), base_url="https://erp.test"
    ) as instance:
        yield instance


async def login(client):
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_login_cookie_rotation_logout_and_immediate_access_revocation(client, env):
    token = await login(client)
    cookie = client.cookies.get("refresh_token")
    cookie_record = env.DB.connection.execute(
        "SELECT token_hash FROM platform_refresh_tokens"
    ).fetchone()[0]
    assert cookie not in cookie_record and cookie_record == token_hash(cookie)
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "admin@example.com"
    assert "core.user.create" in me.json()["permissions"]
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200 and client.cookies.get("refresh_token") != cookie
    assert "HttpOnly" in refresh.headers["set-cookie"] and "Secure" in refresh.headers["set-cookie"]
    assert await client.post("/api/auth/logout")
    assert (await client.post("/api/auth/refresh")).status_code == 401
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_old_refresh_reuse_revokes_the_whole_session(client):
    access = await login(client)
    old = client.cookies.get("refresh_token")
    current = await client.post("/api/auth/refresh")
    assert current.status_code == 200
    reused = await client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={old}"})
    assert reused.status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refresh_has_one_winner_and_reuse_revokes_access(client, env):
    await login(client)
    cookie = client.cookies.get("refresh_token")
    arrived = 0
    both_read_old_token = asyncio.Event()

    async def before_batch(statements):
        nonlocal arrived
        if any("UPDATE platform_refresh_tokens SET used_at" in s.sql for s in statements):
            arrived += 1
            if arrived == 2:
                both_read_old_token.set()
            await asyncio.wait_for(both_read_old_token.wait(), timeout=2)

    env.DB.before_batch = before_batch
    results = await asyncio.gather(
        *(
            client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={cookie}"})
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    assert arrived == 2  # Both read the unconsumed token before either batch committed.
    assert (
        env.DB.connection.execute("SELECT COUNT(*) FROM platform_refresh_tokens").fetchone()[0] == 2
    )
    access = next(r.json()["access_token"] for r in results if r.status_code == 200)
    assert (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_login_failure_budget_covers_simultaneous_attempts_and_persists():
    storage = Storage()
    timestamp = [100]
    guard = LoginBudget(storage, clock=lambda: timestamp[0])
    permits = await asyncio.gather(*(guard.reserve() for _ in range(15)))
    assert sum(p is not None for p in permits) == 10
    for permit in filter(None, permits):
        await guard.finish(permit, False)
    restarted = LoginBudget(storage, clock=lambda: timestamp[0])
    assert await restarted.reserve() is None
    timestamp[0] += 301
    assert await restarted.reserve() is not None


@pytest.mark.asyncio
async def test_wrong_origin_bad_credentials_and_no_token_preserve_error_contract(client):
    rejected = await client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
        headers={"Origin": "https://attacker.example"},
    )
    assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "forbidden"
    bad = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "authentication_failed"
    missing = await client.get("/api/auth/me")
    assert missing.status_code == 401
    invalid = await client.post("/api/auth/login", json={"email": "invalid", "password": "PRIVATE"})
    assert invalid.status_code == 422 and "PRIVATE" not in invalid.text


@pytest.mark.asyncio
async def test_bootstrap_is_sealed_after_creation_and_disabled_outside_staging(client, env):
    body = {"email": "new@example.com", "password": "password123", "full_name": "New"}
    expect = await client.post("/internal/bootstrap", json=body)
    assert expect.status_code == 403
    duplicate = await client.post(
        "/internal/bootstrap", json=body, headers={"Authorization": "Bearer " + env.BOOTSTRAP_TOKEN}
    )
    assert duplicate.status_code == 409
    env.ENVIRONMENT = "production"
    hidden = await client.post(
        "/internal/bootstrap", json=body, headers={"Authorization": "Bearer " + env.BOOTSTRAP_TOKEN}
    )
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_account_deactivation_takes_effect_for_existing_access_and_refresh(client, env):
    access = await login(client)
    env.DB.connection.execute("UPDATE core_users SET is_active=0,_revision=_revision+1")
    assert (
        await client.get("/api/auth/me", headers={"Authorization": "Bearer " + access})
    ).status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_deactivation_between_password_check_and_login_commit_is_atomic(client, env):
    async def before_batch(statements):
        if any("INSERT INTO platform_sessions" in s.sql for s in statements):
            env.DB.connection.execute("UPDATE core_users SET is_active=0")

    env.DB.before_batch = before_batch
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert response.status_code == 401
    assert env.DB.connection.execute("SELECT COUNT(*) FROM platform_sessions").fetchone()[0] == 0
    assert env.DB.connection.execute("SELECT last_login_at FROM core_users").fetchone()[0] is None


@pytest.mark.asyncio
async def test_deactivation_between_refresh_read_and_claim_has_no_partial_token(client, env):
    await login(client)

    async def before_batch(statements):
        if any("UPDATE platform_refresh_tokens SET used_at" in s.sql for s in statements):
            env.DB.connection.execute("UPDATE core_users SET is_active=0")

    env.DB.before_batch = before_batch
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert (
        env.DB.connection.execute("SELECT COUNT(*) FROM platform_refresh_tokens").fetchone()[0] == 1
    )
    assert (
        env.DB.connection.execute("SELECT used_at FROM platform_refresh_tokens").fetchone()[0]
        is None
    )
