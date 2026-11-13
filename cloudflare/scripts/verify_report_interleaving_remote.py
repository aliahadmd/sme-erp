"""Force a real D1 void between report pages, behind staging-only admin gates."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main():
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    bootstrap = json.loads((ROOT / ".secrets.app-staging.json").read_text())["BOOTSTRAP_TOKEN"]
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, f"Login returned {response.status_code}"
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def call(method, path, body=None, *, expected=200, proof=False):
            headers = dict(auth)
            if method != "GET":
                headers["Idempotency-Key"] = uuid.uuid4().hex
            if proof:
                headers["x-bootstrap-token"] = bootstrap
            response = await client.request(method, path, headers=headers, json=body)
            assert response.status_code == expected, (
                f"{path}: {response.status_code}: {response.text[:400]}"
            )
            return response.json()

        party = await call(
            "POST",
            "/api/crm/contacts",
            {"name": f"Report cut {run}", "is_customer": True},
            expected=201,
        )
        tax = await call(
            "POST",
            "/api/catalog/taxes",
            {"code": f"C{run}", "name": "Interleaving", "rate_pct": "0"},
            expected=201,
        )
        invoice = await call(
            "POST",
            "/api/invoicing/invoices",
            {
                "invoice_type": "ar",
                "party_id": party["id"],
                "lines": [
                    {
                        "description": f"Cut line {i}",
                        "qty": "1",
                        "unit_price": "0.01",
                        "tax_id": tax["id"],
                    }
                    for i in range(501)
                ],
            },
            expected=201,
        )
        await call("POST", f"/api/invoicing/invoices/{invoice['id']}/post")
        result = await call(
            "POST",
            "/internal/proofs/reports/void-after-first-page",
            {"invoice_id": invoice["id"]},
            proof=True,
        )
        assert result["changed"] and result["page_queries"] >= 3
        assert int(result["after_epoch"]) > int(result["before_epoch"])
        assert not any(row["tax_code"] == tax["code"] for row in result["rows"])
        final = await call("GET", f"/api/invoicing/invoices/{invoice['id']}")
        assert final["status"] == "void"
        print(
            "PASS: real D1 void after a partial report page forces a complete retry; "
            "no stale prefix survives",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
