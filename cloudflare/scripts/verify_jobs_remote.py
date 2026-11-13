"""Verify real Queue/Cron delivery in isolated staging with outbound mail disabled.

Synthetic documents are reversed after delivery. No credentials, mail bodies or
tokens are printed. Optional --replay-event tests a separately injected poison
event after its subscriber/source has been repaired in the staging database.
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


async def main(replay_event=None, resume=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    private = json.loads((ROOT / ".secrets.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        response = await client.post("/api/auth/login", json=credentials)
        assert response.status_code == 200, f"Login returned {response.status_code}"
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def call(method, path, body=None, expected=200, *, headers=None):
            selected = {**auth, **(headers or {})}
            if method != "GET":
                selected.setdefault("Idempotency-Key", uuid.uuid4().hex)
            response = await client.request(method, path, headers=selected, json=body)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}: {response.text[:250]}"
            )
            return response.json() if response.content else None

        async def wait_for(check, *, timeout=240):
            started = time.monotonic()
            while True:
                result = await check()
                if result:
                    return result
                if time.monotonic() - started >= timeout:
                    raise AssertionError("Timed out waiting for the staging background worker")
                await asyncio.sleep(3)

        assert (await call("GET", "/api/meta"))["environment"] == "staging"
        initial = await call("GET", "/api/jobs/status")
        assert initial["enabled"] and initial["email_mode"] in {"disabled", "test"}

        if replay_event:
            letters = await call("GET", "/api/jobs/dead-letters?limit=100")
            target = next(
                item
                for item in letters["items"]
                if item["event_id"] == replay_event and item["state"] == "quarantined"
            )
            key = uuid.uuid4().hex
            result = await call(
                "POST",
                f"/api/jobs/dead-letters/{target['id']}/replay",
                headers={"Idempotency-Key": key},
            )
            assert result["event_id"] == replay_event
            duplicate = await call(
                "POST",
                f"/api/jobs/dead-letters/{target['id']}/replay",
                headers={"Idempotency-Key": key},
            )
            assert duplicate == result

            async def resolved():
                data = await call("GET", "/api/jobs/dead-letters?limit=100")
                return next(
                    (
                        r
                        for r in data["items"]
                        if r["id"] == target["id"] and r["state"] == "resolved"
                    ),
                    None,
                )

            await wait_for(resolved)
            print("PASS actual DLQ replay, new delivery generation and duplicate admin command")
            return

        async def create(path, data):
            return await call("POST", path, data, expected=201)

        checkpoint = ROOT / ".cloudflare/verification/jobs-pending.json"
        if resume:
            pending = json.loads(checkpoint.read_text())
            invoice, payment, product, delivery, receipt = (
                {"id": pending[key]}
                for key in ["invoice_id", "payment_id", "product_id", "delivery_id", "receipt_id"]
            )
        else:
            party = await create(
                "/api/crm/contacts",
                {
                    "name": f"Queue proof {run}",
                    "is_customer": True,
                    "is_supplier": True,
                    "emails": [{"value": f"queue-proof-{run}@example.com"}],
                },
            )
            warehouse = await create(
                "/api/inventory/warehouses",
                {
                    "code": "Q" + run,
                    "name": f"Queue proof {run}",
                },
            )
            product = await create(
                "/api/catalog/products",
                {
                    "name": f"Queue widget {run}",
                    "sale_price": "20",
                    "cost_price": "10",
                    "min_stock": "5",
                },
            )
            receipt = await create(
                "/api/inventory/receipts",
                {
                    "warehouse_id": warehouse["id"],
                    "lines": [{"product_id": product["id"], "qty": "6", "unit_cost": "10"}],
                },
            )
            await call("POST", f"/api/inventory/receipts/{receipt['id']}/post")
            delivery = await create(
                "/api/inventory/deliveries",
                {
                    "warehouse_id": warehouse["id"],
                    "party_id": party["id"],
                    "lines": [{"product_id": product["id"], "qty": "2", "unit_price": "20"}],
                },
            )
            await call("POST", f"/api/inventory/deliveries/{delivery['id']}/post")
            invoice = await create(
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": party["id"],
                    "due_date": "2020-01-01",
                    "lines": [
                        {"description": f"Queue service {run}", "qty": "1", "unit_price": "10"}
                    ],
                },
            )
            await call("POST", f"/api/invoicing/invoices/{invoice['id']}/post")
            payment = await create(
                "/api/invoicing/payments",
                {
                    "direction": "in",
                    "party_id": party["id"],
                    "amount": "1",
                    "method": "bank",
                    "allocations": [{"invoice_id": invoice["id"], "amount": "1"}],
                },
            )
            await call("POST", f"/api/invoicing/invoices/{invoice['id']}/send-email")

            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(
                json.dumps(
                    {
                        "invoice_id": invoice["id"],
                        "payment_id": payment["id"],
                        "product_id": product["id"],
                        "delivery_id": delivery["id"],
                        "receipt_id": receipt["id"],
                    },
                    indent=2,
                )
                + "\n"
            )

        expected = {"invoice_posted", "invoice_overdue", "payment_recorded", "low_stock"}

        async def delivered():
            notes = await call("GET", "/api/notifications?limit=100")
            relevant = [
                r
                for r in notes["items"]
                if (
                    (r["payload"] or {}).get("invoice_id") == invoice["id"]
                    or (r["payload"] or {}).get("payment_id") == payment["id"]
                    or (r["payload"] or {}).get("product_id") == product["id"]
                )
            ]
            return relevant if {r["type"] for r in relevant} == expected else None

        notices = await wait_for(delivered)
        assert len(notices) == 4
        print(
            "PASS actual Queue delivery: invoice, overdue, payment and low-stock notices",
            flush=True,
        )
        result = await call(
            "POST",
            "/internal/jobs/tick",
            {"force_maintenance": True},
            headers={"X-Bootstrap-Token": private["BOOTSTRAP_TOKEN"]},
        )
        assert all(result[k]["status"] == "complete" for k in ["quotes", "audit", "overdue"])
        print("PASS bounded scheduled maintenance and durable safe-mode mail intents", flush=True)

        # Do not count the manual proof as evidence for automatic Cron execution.
        manual_trigger = (await call("GET", "/api/jobs/status"))["scheduler"]["last_trigger_at"]

        async def cron_executed():
            state = (await call("GET", "/api/jobs/status"))["scheduler"]
            return (
                state
                if state
                and state["last_trigger_at"] != manual_trigger
                and (state["last_success_at"] and state["last_error"] is None)
                else None
            )

        cron = await wait_for(cron_executed, timeout=1080)
        print("PASS automatic Cron invocation and successful D1 checkpoint", flush=True)

        for path in [
            f"/api/invoicing/payments/{payment['id']}/void",
            f"/api/invoicing/invoices/{invoice['id']}/void",
            f"/api/inventory/deliveries/{delivery['id']}/void",
            f"/api/inventory/receipts/{receipt['id']}/void",
        ]:
            current = await call("GET", path.removesuffix("/void"))
            if current["status"] != "void":
                await call("POST", path)
        proof = {
            "invoice_id": invoice["id"],
            "payment_id": payment["id"],
            "product_id": product["id"],
            "delivery_id": delivery["id"],
            "receipt_id": receipt["id"],
            "notification_ids": [r["id"] for r in notices],
            "cron": cron,
        }
        target = ROOT / ".cloudflare/verification/jobs.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(proof, indent=2) + "\n")
        print("PASS all synthetic financial and stock effects reversed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-event")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.replay_event, args.resume))
