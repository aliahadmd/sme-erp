"""Cron-driven exact capture/recovery of app-staging's larger existing dataset.

Uses only the configured dedicated capacity target; never resets a database or
creates financial fixtures. Resume the same saved jobs after a process/network
interruption. Cron, not an HTTP advance loop, drives all progress.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
TARGET = "bd4015b2-46eb-48d4-baef-8651420ef6f3"
DIRECTORY = ROOT / ".cloudflare/verification/backup-capacity"
CHECKPOINT = DIRECTORY / "capture-recovery.json"


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


async def main(resume=False):
    if resume:
        proof = json.loads(CHECKPOINT.read_text())
    else:
        assert not CHECKPOINT.exists(), "Inspect/resume the existing capacity proof"
        proof = {
            "run": uuid.uuid4().hex,
            "url": URL,
            "target": TARGET,
            "started_at": datetime.now(UTC).isoformat(),
        }
    assert proof["url"] == URL and proof["target"] == TARGET
    assert not proof.get("complete") or proof.get("refresh"), "This proof already completed"
    save(proof)
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0"),
    ) as client:
        if proof.get("refresh"):
            client.cookies.set("refresh_token", proof["refresh"], path="/api/auth")
            logout = await client.post("/api/auth/logout")
            assert logout.status_code == 200, "Wait for the old proof session to be revocable"
            proof.pop("refresh")
            client.cookies.clear()
            save(proof)
        if proof.get("complete"):
            print("Revoked the completed proof's remaining session")
            return

        async def login():
            if proof.get("refresh"):
                response = await client.post("/api/auth/refresh")
                if response.status_code == 401:
                    proof.pop("refresh")
                    client.cookies.clear()
                    response = await client.post("/api/auth/login", json=credentials)
            else:
                response = await client.post("/api/auth/login", json=credentials)
            assert response.status_code == 200, f"Login: {response.status_code}"
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            proof["refresh"] = client.cookies.get("refresh_token")
            save(proof)

        await login()

        async def call(method, path, *, name=None, expected=200):
            headers = (
                {"Idempotency-Key": "capacity-backup:" + proof["run"] + ":" + name} if name else {}
            )
            assert method == "GET" or name
            for attempt in range(3):
                try:
                    response = await client.request(method, path, headers=headers)
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            if response.status_code == 401:
                await login()
                response = await client.request(method, path, headers=headers)
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json()

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(row["total_debit"]) for row in rows) == sum(
                Decimal(row["total_credit"]) for row in rows
            )
            return {row["code"]: row["balance"] for row in rows}

        async def finish(path, label):
            started, prior = time.monotonic(), None
            while True:
                status = await call("GET", path)
                proof[label] = status
                save(proof)
                assert status["status"] != "failed", f"{label}: {status.get('last_error')}"
                if status["status"] == "complete":
                    return status
                marker = tuple(
                    status.get(k) for k in ["status", "phase", "table_index", "page_index"]
                )
                if marker != prior:
                    print(label + ": " + str(marker), flush=True)
                    prior = marker
                assert time.monotonic() - started < 1800, (
                    "Observe the saved job; never start a duplicate"
                )
                await asyncio.sleep(5)

        try:
            assert (await call("GET", "/api/meta"))["environment"] == "staging"
            assert (await call("GET", "/api/org"))["id"] == ORG
            jobs = await call("GET", "/api/jobs/status")
            assert jobs["enabled"] and jobs["email_mode"] == "test"
            if "baseline" not in proof:
                proof["baseline"] = await books()
                save(proof)
            if "backup_id" not in proof:
                assert not jobs["maintenance"]["active"]
                created = await call("POST", "/api/backups", name="backup", expected=202)
                proof["backup_id"] = created["id"]
                save(proof)
            backup = await finish("/api/backups/" + proof["backup_id"], "backup")
            assert not (await call("GET", "/api/jobs/status"))["maintenance"]["active"]
            assert {"id": backup["id"]} in (await call("GET", "/api/backup-archive"))["items"]
            print(f"PASS committed capture: {backup['rows']} rows/{backup['page_index']} pages")
            if "restore_id" not in proof:
                requested = await call(
                    "POST",
                    "/api/backups/" + backup["id"] + "/restore",
                    name="restore",
                    expected=202,
                )
                proof["restore_id"] = requested["id"]
                save(proof)
            current = await call("GET", "/api/recoveries/" + proof["restore_id"])
            assert current["target_database_id"] == TARGET, "Unexpected recovery binding"
            recovered = await finish("/api/recoveries/" + proof["restore_id"], "recovery")
            result = recovered["result"]
            assert all(
                result[key]
                for key in [
                    "exact_rows_verified",
                    "schema_verified",
                    "foreign_keys_clean",
                    "sessions_revoked",
                    "external_delivery_held",
                ]
            )
            assert result["tables"] == 62 and int(result["rows"]) == int(backup["rows"])
            assert await books() == proof["baseline"]
            proof["health"] = await call("GET", "/api/operations/health")
            assert proof["health"]["status"] == "ok", "Recovery readiness is not healthy"
            proof.update(complete=True, finished_at=datetime.now(UTC).isoformat())
            save(proof)
            print(
                "PASS exact isolated recovery, revoked target sessions, external holds "
                "and source balances"
            )
        finally:
            logout = await client.post("/api/auth/logout")
            proof["logout_status"] = logout.status_code
            if logout.status_code == 200:
                proof.pop("refresh", None)
            save(proof)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.resume))
