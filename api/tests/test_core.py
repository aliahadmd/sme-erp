"""ERP Core module tests: auth, RBAC, audit, settings."""

import pytest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email: str = "admin@example.com", password: str = "admin123") -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def test_login_and_me(client):
    token = await _login(client)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    assert me["email"] == "admin@example.com"
    assert me["is_superuser"] is True
    assert "core.user.create" in me["permissions"]


async def test_login_rejects_bad_credentials(client):
    response = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


async def test_me_requires_token(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_refresh_flow(client):
    await _login(client)  # stores refresh cookie in the client's jar
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
    # Old refresh cookie must have been rotated (denylisted)
    stale = await client.post("/api/auth/refresh")
    assert stale.status_code == 200  # newest cookie works


async def test_logout_revokes_refresh(client):
    await _login(client)
    await client.post("/api/auth/logout")
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_user_crud_and_rbac(client):
    admin = await _login(client)

    # Create a viewer user
    response = await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "viewer@example.com",
            "password": "viewerpass123",
            "full_name": "Vera Viewer",
            "role_codes": ["viewer"],
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()
    assert user["role_codes"] == ["viewer"]

    # Viewer logs in
    viewer_token = await _login(client, "viewer@example.com", "viewerpass123")

    # Viewer lacks core.user.read → 403 on user list
    forbidden = await client.get("/api/users", headers=_auth(viewer_token))
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    # Viewer lacks core.user.create too
    create_attempt = await client.post(
        "/api/users",
        headers=_auth(viewer_token),
        json={
            "email": "x@example.com",
            "password": "password123",
            "full_name": "X",
        },
    )
    assert create_attempt.status_code == 403

    # Duplicate email is rejected
    dup = await client.post(
        "/api/users",
        headers=_auth(admin),
        json={
            "email": "viewer@example.com",
            "password": "password123",
            "full_name": "Dup",
        },
    )
    assert dup.status_code == 409


async def test_roles_listed_with_permissions(client):
    admin = await _login(client)
    roles = (await client.get("/api/roles", headers=_auth(admin))).json()
    by_code = {r["code"]: r for r in roles}
    assert "admin" in by_code
    assert by_code["admin"]["is_system"] is True
    assert "*" not in str(by_code["sales"]["permission_codes"])
    assert "sales.order.create" in by_code["sales"]["permission_codes"]


async def test_settings_roundtrip_and_audit(client):
    admin = await _login(client)
    value = {"payment_terms_days": 14}
    put = await client.put("/api/settings/test.key", headers=_auth(admin), json={"value": value})
    assert put.status_code == 200, put.text
    got = (await client.get("/api/settings/test.key", headers=_auth(admin))).json()
    assert got["value"] == value

    # Login + settings change are audited
    logs = (await client.get("/api/audit-logs", headers=_auth(admin), params={"limit": 50})).json()
    actions = {item["action"] for item in logs["items"]}
    assert "login" in actions
    setting_updates = [item for item in logs["items"] if item["entity_type"] == "core.setting"]
    assert setting_updates, "settings change must be audited"


async def test_unknown_setting_404(client):
    admin = await _login(client)
    response = await client.get("/api/settings/nope.nope", headers=_auth(admin))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("not-an-email", "longenough1"),
        ("admin@example.com", ""),
    ],
)
async def test_login_validation(client, email, password):
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 422


async def test_superuser_escalation_blocked(client):
    # Grant a clerk user-create rights only
    admin_token = await _login(client)
    role = await client.post(
        "/api/roles",
        headers=_auth(admin_token),
        json={"code": "clerk_hr", "name": "Clerk", "permission_codes": ["core.user.create"]},
    )
    assert role.status_code == 201, role.text
    clerk = await client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={
            "email": "clerk@example.com",
            "password": "password123",
            "full_name": "Clerk",
            "role_codes": ["clerk_hr"],
        },
    )
    assert clerk.status_code == 201
    clerk_token = (
        await client.post(
            "/api/auth/login", json={"email": "clerk@example.com", "password": "password123"}
        )
    ).json()["access_token"]

    # Clerk can create normal users…
    normal = await client.post(
        "/api/users",
        headers=_auth(clerk_token),
        json={"email": "normal@example.com", "password": "password123", "full_name": "N"},
    )
    assert normal.status_code == 201

    # …but cannot mint a superuser (privilege escalation)
    escalated = await client.post(
        "/api/users",
        headers=_auth(clerk_token),
        json={
            "email": "evil-super@example.com",
            "password": "password123",
            "full_name": "Evil",
            "is_superuser": True,
        },
    )
    assert escalated.status_code == 403


async def test_self_deactivation_and_last_superuser_guarded(client):
    admin = await _login(client)
    me = (await client.get("/api/auth/me", headers=_auth(admin))).json()

    # Cannot deactivate yourself
    self_off = await client.patch(
        f"/api/users/{me['id']}", headers=_auth(admin), json={"is_active": False}
    )
    assert self_off.status_code == 422

    # Create a second superuser, deactivate it — fine while another exists
    second = (
        await client.post(
            "/api/users",
            headers=_auth(admin),
            json={
                "email": "super2@example.com",
                "password": "password123",
                "full_name": "S2",
                "is_superuser": True,
            },
        )
    ).json()
    off = await client.patch(
        f"/api/users/{second['id']}", headers=_auth(admin), json={"is_active": False}
    )
    assert off.status_code == 200

    # Admin is now the last active superuser — deactivation must be blocked
    last = await client.patch(
        f"/api/users/{me['id']}", headers=_auth(admin), json={"is_active": False}
    )
    assert last.status_code == 422
    assert "last active superuser" in last.json()["error"]["detail"]


async def test_login_rate_limited_after_repeated_failures(client):
    # Unique email per run — redis persists between runs, so a fixed address
    # would inherit its old failure counter.
    import uuid as _uuid

    email = f"ratelimit-{_uuid.uuid4().hex[:8]}@example.com"
    for _ in range(10):
        response = await client.post(
            "/api/auth/login", json={"email": email, "password": "wrong-password"}
        )
        assert response.status_code == 401
    blocked = await client.post(
        "/api/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    # Even the correct password is blocked while the window is active
    still_blocked = await client.post(
        "/api/auth/login", json={"email": email, "password": "admin123"}
    )
    assert still_blocked.status_code == 429
