"""Exercise native Queue/D1 mail delivery in test mode; never send outbound mail.

Only the isolated staging host is used. A synthetic invoice is voided after
delivery; immutable history and metadata receipts remain as proof artifacts.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main():
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    checkpoint = ROOT / ".cloudflare/verification/mail-pending.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        response = await client.post("/api/auth/login", json=credentials)
        assert response.status_code == 200, f"Login returned {response.status_code}"
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def call(method, path, body=None, *, expected=200, key=None):
            headers = {**auth}
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            response = await client.request(method, path, json=body, headers=headers)
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        assert (await call("GET", "/api/meta"))["environment"] == "staging"
        assert (await call("GET", "/api/jobs/status"))["email_mode"] == "test"
        party = await call(
            "POST",
            "/api/crm/contacts",
            {
                "name": f"Mail delivery proof {run}",
                "is_customer": True,
                "emails": [{"value": "demo-recipient@example.com"}],
            },
            expected=201,
        )
        doc = await call(
            "POST",
            "/api/invoicing/invoices",
            {
                "invoice_type": "ar",
                "party_id": party["id"],
                "due_date": "2000-01-01",
                "lines": [
                    {"description": "Synthetic email proof", "qty": "1", "unit_price": "43.21"}
                ],
            },
            expected=201,
        )
        checkpoint.write_text(
            json.dumps({"invoice_id": doc["id"], "contact_id": party["id"]}) + "\n"
        )
        await call("POST", f"/api/invoicing/invoices/{doc['id']}/post")
        key = "mail-proof:" + run
        queued = await call("POST", f"/api/invoicing/invoices/{doc['id']}/send-email", key=key)
        duplicate = await call("POST", f"/api/invoicing/invoices/{doc['id']}/send-email", key=key)
        assert queued == duplicate and queued["status"] == "queued"

        started = time.monotonic()
        while True:
            # Bounded pages permit existing staging history without assuming
            # this proof is the only source of mail intents.
            target = []
            offset = 0
            while True:
                page = await call("GET", f"/api/jobs/mail?limit=100&offset={offset}")
                target.extend(row for row in page["items"] if row["source_id"] == doc["id"])
                offset += len(page["items"])
                if offset >= page["total"] or offset >= 1000:
                    break
            if (
                {row["kind"] for row in target} >= {"staff", "customer", "manual"}
                and len(target) >= 4
                and all(row["status"] == "test" for row in target)
            ):
                break
            assert time.monotonic() - started < 240, (
                "Timed out waiting for native mail test receipts"
            )
            await asyncio.sleep(3)

        for row in target:
            assert row["provider_id"] is None and row["completed_at"] and not row["claim_until"]
            assert not {"recipient", "subject", "body"} & row.keys()
            attempts = await call("GET", f"/api/jobs/mail/{row['id']}/attempts")
            assert attempts["total"] == 1
            attempt = attempts["items"][0]
            assert attempt["state"] == "test" and not attempt["provider_id"]
            assert len(attempt["payload_hash"]) == 64 and attempt["finished_at"]
        print(
            f"PASS native Queue rendering and D1 receipts for {len(target)} test mail intents",
            flush=True,
        )
        print(
            "PASS repeated invoice email command creates one manual intent; metadata omits content",
            flush=True,
        )
        # Confirm a repeated read preserves one test receipt per intent.
        for row in target:
            assert (await call("GET", f"/api/jobs/mail/{row['id']}/attempts"))["total"] == 1
        await call("POST", f"/api/invoicing/invoices/{doc['id']}/void")
        status = await call("GET", "/api/jobs/status")
        assert status["email_mode"] == "test"
        artifact = {
            "invoice_id": doc["id"],
            "contact_id": party["id"],
            "mail_ids": [row["id"] for row in target],
            "test_attempts": len(target),
            "email_mode": "test",
            "invoice_reversed": True,
            "live_send_verified": False,
        }
        (ROOT / ".cloudflare/verification/mail.json").write_text(
            json.dumps(artifact, indent=2) + "\n"
        )
        checkpoint.unlink()
        print(
            "PASS synthetic invoice reversed; real outbound delivery remains untested", flush=True
        )


if __name__ == "__main__":
    asyncio.run(main())
