"""Seed/resume the isolated fresh demo, retaining credentials and progress privately.

Initialize the empty D1 with apply_migrations.py first. The first deployment
temporarily enables ERP_DEMO_BOOTSTRAP=true and disables ordinary jobs. After
--bootstrap and this recipe pass, rebuild without that variable to close the
bootstrap route and enable Queue/Cron processing. Production is not selectable.
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from demo_seed import VERSION, DemoSeed

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-demo-staging-20261114.aliahad.workers.dev"
ORG_ID = "01a1d6bf-eca3-7654-b225-ae37c723ed96"
CHECKPOINT = ROOT / ".cloudflare/verification/demo-seed.json"


async def main(bootstrap=False, ipv4=False):
    credentials = json.loads((ROOT / ".demo.demo-staging.json").read_text())
    checkpoint = (
        json.loads(CHECKPOINT.read_text())
        if CHECKPOINT.exists()
        else {
            "version": VERSION,
            "url": URL,
            "date": (datetime.now(UTC) + timedelta(hours=8)).date().isoformat(),
        }
    )
    assert checkpoint["version"] == VERSION and checkpoint["url"] == URL
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

    def save():
        temporary = CHECKPOINT.with_suffix(".tmp")
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as out:
            out.write(json.dumps(checkpoint, indent=2) + "\n")
        temporary.replace(CHECKPOINT)

    save()
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0" if ipv4 else None),
    ) as client:
        meta = await client.get("/api/meta")
        assert meta.status_code == 200 and meta.json()["environment"] == "staging"
        if bootstrap:
            private = json.loads((ROOT / ".secrets.demo-staging.json").read_text())
            response = await client.post(
                "/internal/bootstrap",
                json=credentials["admin"],
                headers={"Authorization": "Bearer " + private["BOOTSTRAP_TOKEN"]},
            )
            assert response.status_code in {201, 409}, f"Bootstrap returned {response.status_code}"
        seed = DemoSeed(client, credentials, checkpoint, save)
        await seed.login()
        org = await seed.read("/api/org")
        assert org["id"] == ORG_ID, "Unexpected target organization"
        try:
            result = await seed.run()
            if not bootstrap:
                closed = await client.post("/internal/bootstrap", json=credentials["admin"])
                assert closed.status_code == 404, (
                    "Demo bootstrap must be closed after initialization"
                )
                jobs = await seed.read("/api/jobs/status")
                assert jobs["enabled"] and jobs["email_mode"] == "test"
            print("PASS coherent demo, exact stock/open balances, balanced books and HR states")
            print(json.dumps(result, indent=2))
        finally:
            response = await client.post("/api/auth/logout")
            assert response.status_code == 200, "Seed session logout failed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--ipv4", action="store_true", help="Use IPv4 on networks with broken IPv6")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.bootstrap, arguments.ipv4))
