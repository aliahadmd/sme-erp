"""Full isolated D1/R2 recovery proof; never sends mail or resets a database.

The backup/restore job IDs and synthetic fixture IDs are checkpointed so a lost
client process can resume with --resume. Restore rejects occupied targets;
use --backup-only when the configured target already contains a prior proof.
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
CHECKPOINT = ROOT / ".cloudflare/verification/backups-pending.json"


async def main(resume=False, backup_only=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    progress = json.loads(CHECKPOINT.read_text()) if resume else {"run": uuid.uuid4().hex}
    if not resume:
        assert not CHECKPOINT.exists(), "Inspect/resume the existing backup proof first"
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

    def save():
        CHECKPOINT.write_text(json.dumps(progress, indent=2) + "\n")

    async with httpx.AsyncClient(
        base_url=URL, timeout=240, transport=httpx.AsyncHTTPTransport(retries=3)
    ) as client:
        auth = {}

        async def sign_in():
            response = await client.post("/api/auth/login", json=credentials)
            assert response.status_code == 200, f"Login returned {response.status_code}"
            auth["Authorization"] = "Bearer " + response.json()["access_token"]

        await sign_in()

        async def call(method, path, body=None, *, expected=200, step=None):
            headers = {**auth}
            if method != "GET":
                headers["Idempotency-Key"] = progress["run"] + ":" + (step or uuid.uuid4().hex)
            response = await client.request(method, path, headers=headers, json=body)
            if response.status_code == 401:
                await sign_in()
                headers.update(auth)
                response = await client.request(method, path, headers=headers, json=body)
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        assert (await call("GET", "/api/meta"))["environment"] == "staging"
        assert (await call("GET", "/api/jobs/status"))["email_mode"] == "test"
        save()
        if "party_id" not in progress:
            party = await call(
                "POST",
                "/api/crm/contacts",
                {
                    "name": "R2 recovery proof " + progress["run"][:10],
                    "is_customer": True,
                },
                expected=201,
                step="party",
            )
            progress["party_id"] = party["id"]
            save()
        if "invoice_id" not in progress:
            doc = await call(
                "POST",
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": progress["party_id"],
                    "lines": [
                        {
                            "description": "18-digit recovery value",
                            "qty": "10000",
                            "unit_price": "999999999999.999999",
                        },
                        *[
                            {"description": f"Ordered line {i}", "qty": "1", "unit_price": "0"}
                            for i in range(1, 12)
                        ],
                    ],
                },
                expected=201,
                step="invoice",
            )
            progress["invoice_id"] = doc["id"]
            save()
        doc = await call("GET", "/api/invoicing/invoices/" + progress["invoice_id"])
        if doc["status"] == "draft":
            doc = await call("POST", f"/api/invoicing/invoices/{doc['id']}/post", step="post")
        assert doc["total"] == "9999999999999999.99"
        assert [line["description"] for line in doc["lines"]][1:] == [
            f"Ordered line {i}" for i in range(1, 12)
        ]
        if "payment_id" not in progress:
            pay = await call(
                "POST",
                "/api/invoicing/payments",
                {
                    "direction": "in",
                    "party_id": progress["party_id"],
                    "amount": "0.01",
                    "method": "bank",
                    "allocations": [{"invoice_id": doc["id"], "amount": "0.01"}],
                },
                expected=201,
                step="payment",
            )
            progress["payment_id"] = pay["id"]
            save()
        if "backup_id" not in progress:
            backup = await call("POST", "/api/backups", expected=202, step="backup")
            progress["backup_id"] = backup["id"]
            save()

        async def finish(path, label):
            started, last = time.monotonic(), None
            while True:
                state = await call("GET", path)
                assert state["status"] != "failed", f"{label}: {state['last_error']}"
                if state["status"] == "complete":
                    return state
                marker = (
                    state["status"],
                    state.get("phase"),
                    state.get("table_index"),
                    state.get("page_index"),
                )
                if marker != last:
                    print(f"{label}: {marker}", flush=True)
                    last = marker
                assert time.monotonic() - started < 1500, f"{label} did not finish in 25 minutes"
                try:
                    await call("POST", "/api/backups/advance")
                except httpx.ReadTimeout:
                    # Cron or another RPC can hold the controller mutex. The
                    # response is unknown, not a failed/restartable job. Observe
                    # the same durable ID again before requesting more work.
                    print(f"{label}: advance response timed out; checking saved job", flush=True)
                await asyncio.sleep(1)

        backup = await finish("/api/backups/" + progress["backup_id"], "Backup")
        assert not (await call("GET", "/api/jobs/status"))["maintenance"]["active"]
        archived = await call("GET", "/api/backup-archive")
        assert {"id": backup["id"]} in archived["items"]
        print(
            f"PASS committed R2 archive: {backup['rows']} rows, {backup['page_index']} pages",
            flush=True,
        )
        result = None
        if not backup_only:
            if "restore_id" not in progress:
                requested = await call(
                    "POST", f"/api/backups/{backup['id']}/restore", expected=202, step="restore"
                )
                progress["restore_id"] = requested["id"]
                save()
            recovered = await finish("/api/recoveries/" + progress["restore_id"], "Recovery")
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
            print(
                "PASS native exact recovery, schema/FK checks, session expiry and external holds",
                flush=True,
            )
        offset, pay = 0, None
        while pay is None:
            payments = await call("GET", f"/api/invoicing/payments?limit=100&offset={offset}")
            pay = next(
                (row for row in payments["items"] if row["id"] == progress["payment_id"]), None
            )
            offset += len(payments["items"])
            assert pay is not None or offset < payments["total"], "Proof payment is missing"
        if pay["status"] == "recorded":
            await call("POST", f"/api/invoicing/payments/{pay['id']}/void", step="void-payment")
        doc = await call("GET", "/api/invoicing/invoices/" + progress["invoice_id"])
        if doc["status"] != "void":
            await call("POST", f"/api/invoicing/invoices/{doc['id']}/void", step="void-invoice")
        artifact = {
            **progress,
            "backup": backup,
            "recovery": result,
            "source_fixture_reversed": True,
        }
        (ROOT / ".cloudflare/verification/backups.json").write_text(
            json.dumps(artifact, indent=2) + "\n"
        )
        CHECKPOINT.unlink()
        print(
            "PASS source financial fixture reversed; target remains isolated for review", flush=True
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--backup-only", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.resume, arguments.backup_only))
