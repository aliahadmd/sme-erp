"""Exercise all AI contracts against Workers AI and isolated staging D1.

Uses synthetic product/report data. Bulk results stay in review; only one
explicit test acceptance writes a generated description. No mail is sent.
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
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        signed_in = await client.post("/api/auth/login", json=credentials)
        assert signed_in.status_code == 200
        auth = {"Authorization": "Bearer " + signed_in.json()["access_token"]}

        async def call(method, path, body=None, *, expected=200, key=None):
            headers = dict(auth)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            response = await client.request(method, path, headers=headers, json=body)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}: {response.text[:300]}"
            )
            return response.json() if response.content else None

        assert (await call("GET", "/api/meta"))["environment"] == "staging"
        initial = await call("GET", "/api/jobs/status")
        assert initial["ai"]["enabled"] and initial["email_mode"] in {"disabled", "test"}
        before = initial["ai"]["budget"]["used"]
        sentinel = "ERP-CF-AI-PROOF-" + run
        echo = await call("POST", "/api/ai/echo", {"prompt": sentinel})
        assert sentinel in echo["text"] and echo["model"].startswith("@cf/")
        summary = await call(
            "POST",
            "/api/ai/summarize",
            {
                "report": "sales",
                "payload": {"currency": "USD", "total_sales": "123.45", "invoice_count": 3},
            },
        )
        assert "123.45" in summary["summary"] and summary["model"] == echo["model"]
        print("PASS actual Cloudflare-hosted echo and decimal-string report summary", flush=True)

        product = await call(
            "POST",
            "/api/catalog/products",
            {
                "name": f"Demo fastening screw {run}",
                "sale_price": "1.00",
            },
            expected=201,
        )
        second = await call(
            "POST",
            "/api/catalog/products",
            {
                "name": f"Demo review item {run}",
                "sale_price": "2.00",
            },
            expected=201,
        )
        proposal = await call("POST", f"/api/catalog/products/{product['id']}/generate-description")
        assert proposal["description"].strip() and proposal["model"] == echo["model"]
        unchanged = await call("GET", "/api/catalog/products/" + product["id"])
        assert unchanged["description"] is None
        keyword = await call(
            "POST",
            "/api/catalog/products",
            {
                "name": f"Unicode search {run}",
                "description": "Straße 铜管",
                "sale_price": "1.00",
            },
            expected=201,
        )
        results = await call("GET", "/api/ai/search?q=STRASSE%20%E9%93%9C%E7%AE%A1")
        assert any(row["id"] == keyword["id"] for row in results)
        print(
            "PASS single-product suggestion remains unapplied; Unicode keyword search", flush=True
        )

        key = uuid.uuid4().hex
        queued = await call("POST", "/api/catalog/products/generate-missing", key=key)
        assert queued["queued"] >= 2
        assert await call("POST", "/api/catalog/products/generate-missing", key=key) == queued
        checkpoint = ROOT / ".cloudflare/verification/ai-pending.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            json.dumps({"product_id": product["id"], "second_id": second["id"]}, indent=2) + "\n"
        )
        started = time.monotonic()
        while True:
            drafts = await call("GET", "/api/ai/drafts")
            by_product = {row["entity_id"]: row for row in drafts}
            if product["id"] in by_product and second["id"] in by_product:
                break
            assert time.monotonic() - started < 420, "Timed out waiting for real AI Queue delivery"
            await asyncio.sleep(3)
        assert (await call("GET", "/api/catalog/products/" + product["id"]))["description"] is None
        target, discard = by_product[product["id"]], by_product[second["id"]]
        assert target["model"] == discard["model"] == echo["model"]
        key = uuid.uuid4().hex
        assert await call("POST", f"/api/ai/drafts/{target['id']}/accept", key=key) == {
            "status": "accepted"
        }
        assert await call("POST", f"/api/ai/drafts/{target['id']}/accept", key=key) == {
            "status": "accepted"
        }
        applied = await call("GET", "/api/catalog/products/" + product["id"])
        assert applied["description"] == target["draft_text"]
        assert await call("POST", f"/api/ai/drafts/{discard['id']}/discard") == {
            "status": "discarded"
        }
        final = await call("GET", "/api/jobs/status")
        quota = final["ai"]["budget"]
        assert before + 5 <= quota["used"] <= quota["limit"]
        artifact = {
            "model": echo["model"],
            "summary": summary["summary"],
            "single_proposal": proposal["description"],
            "bulk_queued": queued["queued"],
            "accepted_id": target["id"],
            "discarded_id": discard["id"],
            "budget": quota,
        }
        (ROOT / ".cloudflare/verification/ai.json").write_text(
            json.dumps(artifact, indent=2) + "\n"
        )
        print(
            "PASS real bulk Queue inference, explicit draft accept/discard and idempotency",
            flush=True,
        )
        print(
            f"PASS durable shared budget: {quota['used']} attempts of {quota['limit']}", flush=True
        )


if __name__ == "__main__":
    asyncio.run(main())
