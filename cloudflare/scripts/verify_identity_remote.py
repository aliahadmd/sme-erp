"""Verify fresh native authorization after batching reads, on app-staging only.

Creates one private temporary staff account/role and one archived test product.
--cleanup reconciles known creation receipts and disables the account/role.
An unfinished run is never overwritten. No mail or financial writes are sent.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from apply_migrations import literal, query

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
DIRECTORY = ROOT / ".cloudflare/verification/identity"
CHECKPOINT = DIRECTORY / "authorization.json"


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


async def main(cleanup_only=False, fresh=False):
    if fresh and CHECKPOINT.exists():
        prior = json.loads(CHECKPOINT.read_text())
        assert prior.get("cleaned"), "Clean the previous run before starting another"
        CHECKPOINT.rename(DIRECTORY / (prior["run"] + ".json"))
    proof = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else None
    if not cleanup_only:
        assert proof is None, "Use --cleanup or --new for an existing checkpoint"
        proof = {
            "run": uuid.uuid4().hex,
            "url": URL,
            "password": secrets.token_urlsafe(32),
            "replacement_password": secrets.token_urlsafe(32),
            "started_at": datetime.now(UTC).isoformat(),
            "checks": [],
            "submitted": [],
        }
    assert proof and proof["url"] == URL
    save(proof)
    prefix = "identity:" + proof["run"] + ":"
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with (
        httpx.AsyncClient(
            base_url=URL,
            timeout=180,
            transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0"),
        ) as admin,
        httpx.AsyncClient(
            base_url=URL,
            timeout=180,
            transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0"),
        ) as staff,
    ):
        login = await admin.post("/api/auth/login", json=credentials)
        assert login.status_code == 200, f"Admin login: {login.status_code}"
        admin.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        async def call(method, path, body=None, *, name=None, expected=200, client=admin):
            assert method == "GET" or name
            if name in {"role", "user", "allowed-product"}:
                proof["submitted"].append(name)
                save(proof)
            for attempt in range(3):
                try:
                    response = await client.request(
                        method,
                        path,
                        json=body,
                        headers={"Idempotency-Key": prefix + name} if name else {},
                    )
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            proof["checks"].append({"method": method, "path": path, "status": response.status_code})
            save(proof)
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        async def receipt(name):
            actor_id = proof["staff_actor_id"] if name == "allowed-product" else proof["actor_id"]
            identity = hashlib.sha256(
                json.dumps([ORG, actor_id, prefix + name]).encode()
            ).hexdigest()
            rows = await asyncio.to_thread(
                query,
                "app-staging",
                "SELECT response_json FROM cf_commands WHERE id=" + literal(identity),
            )
            assert rows or name not in proof["submitted"], (
                "Missing creation receipt: inspect the original request before cleanup"
            )
            return json.loads(rows[0]["response_json"])["body"] if rows else None

        async def cleanup():
            user = await receipt("user")
            role = await receipt("role")
            product = await receipt("allowed-product") if proof.get("staff_actor_id") else None
            # Creation responses are synchronous/coordinator guarded. Inspect
            # receipts for interrupted creation; never POST to discover IDs.
            if user:
                await call(
                    "PATCH", "/api/users/" + user["id"], {"is_active": False}, name="disable"
                )
            if role:
                await call(
                    "PATCH", "/api/roles/" + role["id"], {"permission_codes": []}, name="revoke"
                )
            if product:
                await call(
                    "POST",
                    "/api/catalog/products/" + product["id"] + "/archive",
                    {"archived": True},
                    name="archive",
                )
            proof.update(cleaned=True, finished_at=datetime.now(UTC).isoformat())
            save(proof)

        try:
            assert (await call("GET", "/api/meta"))["environment"] == "staging"
            assert (await call("GET", "/api/org"))["id"] == ORG
            jobs = await call("GET", "/api/jobs/status")
            assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]
            me = await call("GET", "/api/auth/me")
            assert me["is_superuser"] and "core.user.create" in me["permissions"]
            proof.setdefault("actor_id", me["id"])
            assert me["id"] == proof["actor_id"]
            save(proof)
            if cleanup_only:
                await cleanup()
                return
            role = await call(
                "POST",
                "/api/roles",
                {
                    "code": "identity_" + proof["run"][:12],
                    "name": "Native identity proof",
                    "permission_codes": ["catalog.product.read", "catalog.product.create"],
                },
                name="role",
                expected=201,
            )
            profile = {
                "email": "identity-" + proof["run"] + "@example.com",
                "password": proof["password"],
            }
            user = await call(
                "POST",
                "/api/users",
                {**profile, "full_name": "Identity proof", "role_codes": [role["code"]]},
                name="user",
                expected=201,
            )
            proof["staff_actor_id"] = user["id"]
            save(proof)
            access = await call("POST", "/api/auth/login", profile, name="login", client=staff)
            staff.headers["Authorization"] = "Bearer " + access["access_token"]
            me = await call("GET", "/api/auth/me", client=staff)
            assert not me["is_superuser"] and me["roles"] == [role["code"]]
            assert me["permissions"] == ["catalog.product.create", "catalog.product.read"]
            await call("GET", "/api/catalog/products", client=staff)
            await call("GET", "/api/users", expected=403, client=staff)
            await call(
                "POST",
                "/api/catalog/products",
                {"name": "Identity proof " + proof["run"]},
                name="allowed-product",
                expected=201,
                client=staff,
            )
            await call("PATCH", "/api/roles/" + role["id"], {"permission_codes": []}, name="revoke")
            assert (await call("GET", "/api/auth/me", client=staff))["permissions"] == []
            await call("GET", "/api/catalog/products", expected=403, client=staff)
            await call(
                "POST",
                "/api/catalog/products",
                {"name": "Must be denied"},
                name="denied-product",
                expected=403,
                client=staff,
            )
            await call(
                "PATCH",
                "/api/users/" + user["id"],
                {"password": proof["replacement_password"]},
                name="reset",
            )
            await call("GET", "/api/auth/me", expected=401, client=staff)
            await call("POST", "/api/auth/refresh", name="refresh", expected=401, client=staff)
            proof["verified"] = True
            save(proof)
            await cleanup()
            print("PASS: native admin/staff permissions, immediate role and session revocation")
        finally:
            proof["staff_logout"] = (await staff.post("/api/auth/logout")).status_code
            proof["admin_logout"] = (await admin.post("/api/auth/logout")).status_code
            save(proof)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    steps = parser.add_mutually_exclusive_group(required=True)
    steps.add_argument("--run", action="store_true")
    steps.add_argument("--cleanup", action="store_true")
    parser.add_argument("--new", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.cleanup, arguments.new))
