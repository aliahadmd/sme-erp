"""Check backup failure/retention and archive import on isolated staging.

--prepare-expiry creates one backup request and saves its ID. After the native
controller has captured pages, an operator expires only that run's maintenance
lease in staging D1. --check-expiry verifies failure and resumed writes. To
exercise retention, the operator ages only this failed control row beyond the
configured retention, then --check-retention waits for Cron cleanup.

--import ID checks discovery and imports a committed archive. To prove recovery
from lost metadata, first remove only that archive's control row/page ledger,
after confirming it has no restore references. This script never resets D1,
changes financial records, sends email, or deletes a committed R2 archive.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
CHECKPOINT = ROOT / ".cloudflare/verification/backup-operations.json"


async def main(step, identity=None):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with httpx.AsyncClient(
        base_url=URL, timeout=240, transport=httpx.AsyncHTTPTransport(retries=3)
    ) as client:
        meta = await client.get("/api/meta")
        assert meta.status_code == 200 and meta.json()["environment"] == "staging"
        login = await client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200, f"Login returned {login.status_code}"
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        async def call(method, path, *, expected=200, key=None):
            response = await client.request(
                method,
                path,
                headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
            )
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json()

        jobs = await call("GET", "/api/jobs/status")
        assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

        def save(value):
            CHECKPOINT.write_text(json.dumps(value, indent=2) + "\n")

        async def in_archive(identity):
            cursor = None
            while True:
                response = await client.get(
                    "/api/backup-archive",
                    headers=headers,
                    params={"cursor": cursor} if cursor else {},
                )
                assert response.status_code == 200
                page = response.json()
                if {"id": identity} in page["items"]:
                    return True
                cursor = page["cursor"]
                if cursor is None:
                    return False

        if step == "prepare-expiry":
            assert not CHECKPOINT.exists(), "Inspect the existing operations checkpoint first"
            proof = {"run": uuid.uuid4().hex}
            # Persist the mutation key before sending. An interrupted prepare can
            # be inspected/replayed without creating a second request.
            save(proof)
            row = await call("POST", "/api/backups", expected=202, key=proof["run"])
            proof["backup_id"] = row["id"]
            save(proof)
            print("Prepared staging expiry probe: " + row["id"], flush=True)
            return
        if step == "import":
            uuid.UUID(identity)
            assert await in_archive(identity), "Committed archive is not discoverable"
            for _ in range(2):
                row = await call("POST", f"/api/backup-archive/{identity}/import")
                assert row == {"id": identity, "status": "complete"}
            row = await call("GET", f"/api/backups/{identity}")
            assert row["status"] == "complete" and int(row["page_index"]) > 0
            print("PASS native archive import and duplicate import: " + identity, flush=True)
            return
        proof = json.loads(CHECKPOINT.read_text())
        identity = proof["backup_id"]
        if step == "check-expiry":
            await call("POST", "/api/backups/advance")
            row = await call("GET", f"/api/backups/{identity}")
            assert row["status"] == "failed" and row["last_error"] == "maintenance_lease_lost"
            assert int(row["page_index"]) > 0, "Probe must have captured partial pages"
            assert not await in_archive(identity), "An expired capture must not be discoverable"
            assert not (await call("GET", "/api/jobs/status"))["maintenance"]["active"]
            proof["expired"] = row
            save(proof)
            print("PASS expired partial capture failed without publication; login/writes resumed")
            return
        assert "expired" in proof, "Verify the expired capture before testing retention"
        started = time.monotonic()
        while True:
            row = await call("GET", f"/api/backups/{identity}")
            if row["status"] == "deleted":
                break
            assert time.monotonic() - started < 600, "Retention has not completed"
            await call("POST", "/api/backups/advance")
            await asyncio.sleep(5)
        assert not await in_archive(identity)
        proof["retained_cleanup"] = row
        save(proof)
        print("PASS native retention completed for the failed partial archive", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    steps = parser.add_mutually_exclusive_group(required=True)
    for step in ["prepare-expiry", "check-expiry", "check-retention"]:
        steps.add_argument("--" + step, action="store_const", const=step, dest="step")
    steps.add_argument("--import", dest="identity")
    arguments = parser.parse_args()
    asyncio.run(main("import" if arguments.identity else arguments.step, arguments.identity))
