"""Verify real ERP authentication on the isolated application Worker.

Uses ignored staging credential files and prints no passwords, cookies or JWTs.
Add --bootstrap only to initialize the authorized fresh demo database.
"""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


def expect(response, status):
    if response.status_code != status:
        code = response.json().get("error", {}).get("code", "unknown")
        raise AssertionError(
            f"{response.request.url.path}: expected {status}, got {response.status_code} ({code})"
        )


async def verify(bootstrap: bool):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with httpx.AsyncClient(base_url=URL, timeout=120) as client:
        if bootstrap:
            secrets = json.loads((ROOT / ".secrets.app-staging.json").read_text())
            seeded = await client.post(
                "/internal/bootstrap",
                json=credentials,
                headers={"Authorization": "Bearer " + secrets["BOOTSTRAP_TOKEN"]},
            )
            if seeded.status_code != 409:
                expect(seeded, 201)
                print(
                    f"PASS fresh demo bootstrap: {seeded.json()['permissions']} permissions, "
                    f"{seeded.json()['roles']} system roles"
                )
        expect(await client.get("/healthz"), 200)
        expect(await client.get("/api/auth/me"), 401)
        expect(
            await client.post(
                "/api/auth/login", json=credentials, headers={"Origin": "https://attacker.example"}
            ),
            403,
        )

        async def login():
            response = await client.post("/api/auth/login", json=credentials)
            expect(response, 200)
            cookie = response.headers.get("set-cookie", "")
            assert all(
                marker in cookie
                for marker in ["HttpOnly", "Secure", "SameSite=lax", "Path=/api/auth"]
            )
            return response.json()["access_token"]

        access = await login()
        me = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + access})
        expect(me, 200)
        profile = me.json()
        assert profile["email"] == credentials["email"] and profile["is_superuser"] is True
        assert "core.user.create" in profile["permissions"] and "admin" in profile["roles"]
        assert set(profile) == {"id", "email", "full_name", "is_superuser", "roles", "permissions"}
        print("PASS native Argon2 login, authorization, cookie flags and existing /me contract")

        old = client.cookies.get("refresh_token")
        expect(await client.post("/api/auth/refresh"), 200)
        assert client.cookies.get("refresh_token") != old
        expect(
            await client.post("/api/auth/refresh", headers={"Cookie": "refresh_token=" + old}), 401
        )
        expect(await client.post("/api/auth/refresh"), 401)
        expect(await client.get("/api/auth/me", headers={"Authorization": "Bearer " + access}), 401)
        print("PASS refresh rotation, stale-token reuse detection and immediate session revocation")

        await login()
        old = client.cookies.get("refresh_token")
        raced = await asyncio.gather(
            *(
                client.post("/api/auth/refresh", headers={"Cookie": "refresh_token=" + old})
                for _ in range(2)
            )
        )
        assert sorted(r.status_code for r in raced) == [200, 401], [r.status_code for r in raced]
        winner = next(r.json()["access_token"] for r in raced if r.status_code == 200)
        expect(await client.get("/api/auth/me", headers={"Authorization": "Bearer " + winner}), 401)
        print("PASS competing refreshes have one winner; confirmed reuse revokes its session")

        access = await login()
        expect(await client.post("/api/auth/logout"), 200)
        expect(await client.post("/api/auth/refresh"), 401)
        expect(await client.get("/api/auth/me", headers={"Authorization": "Bearer " + access}), 401)
        print("PASS logout revokes refresh and existing access authorization")

        missing = {"email": f"missing-{uuid4().hex}@example.com", "password": "invalid-password"}
        failures = await asyncio.gather(
            *(client.post("/api/auth/login", json=missing) for _ in range(15))
        )
        statuses = [r.status_code for r in failures]
        assert statuses.count(401) == 10 and statuses.count(429) == 5, statuses
        print("PASS Durable Object failure budget bounds 15 concurrent unknown-user login attempts")


if __name__ == "__main__":
    if sys.argv[1:] not in [[], ["--bootstrap"]]:
        raise SystemExit("Usage: uv run python -u scripts/verify_auth_remote.py [--bootstrap]")
    asyncio.run(verify("--bootstrap" in sys.argv))
