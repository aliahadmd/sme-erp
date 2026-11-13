"""Verify an operator-held lease on isolated staging; never pause production.

Prepare creates an authenticated synthetic contact and an ignored 0600 session
checkpoint. The operator then takes a short fenced lease in staging D1 and runs
--paused. Release that lease (or wait for expiry), then run --released. The
verifier never prints credentials/tokens or creates an unbounded write pause.
"""

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
CHECKPOINT = ROOT / ".cloudflare/verification/maintenance-session.json"


async def main(step):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with httpx.AsyncClient(
        base_url=URL, timeout=120, transport=httpx.AsyncHTTPTransport(retries=3)
    ) as client:
        meta = await client.get("/api/meta")
        assert meta.status_code == 200 and meta.json()["environment"] == "staging"
        if step == "prepare":
            assert not CHECKPOINT.exists(), "Finish or inspect the existing proof session first"
            response = await client.post("/api/auth/login", json=credentials)
            assert response.status_code == 200, f"Login returned {response.status_code}"
            token = response.json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            response = await client.post(
                "/api/crm/contacts",
                headers=headers,
                json={
                    "name": "Maintenance proof " + uuid.uuid4().hex[:10],
                    "is_customer": True,
                },
            )
            assert response.status_code == 201
            CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(
                os.open(CHECKPOINT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
            ) as output:
                output.write(
                    json.dumps(
                        {
                            "token": token,
                            "contact_id": response.json()["id"],
                            "refresh": client.cookies.get("refresh_token"),
                        }
                    )
                )
            print("PASS prepared private staging session and synthetic contact", flush=True)
            return
        session = json.loads(CHECKPOINT.read_text())
        headers = {"Authorization": "Bearer " + session["token"]}
        path = "/api/crm/contacts/" + session["contact_id"]
        client.cookies.set("refresh_token", session["refresh"], path="/api/auth")
        status = await client.get("/api/jobs/status", headers=headers)
        assert status.status_code == 200
        active = bool(status.json()["maintenance"]["active"])
        assert active == (step == "paused"), "Unexpected maintenance state"
        assert (await client.get(path, headers=headers)).status_code == 200
        changed = await client.patch(
            path, headers=headers, json={"name": "Maintenance proof checked"}
        )
        if step == "paused":
            responses = [
                changed,
                await client.post("/api/auth/login", json=credentials),
                await client.post("/api/auth/refresh"),
                await client.post("/api/auth/logout"),
            ]
            for response in responses:
                assert response.status_code == 503, f"Write returned {response.status_code}"
                assert response.json()["error"]["code"] == "maintenance"
            print(
                "PASS native reads remain available; command, login, refresh and logout are paused",
                flush=True,
            )
        else:
            assert changed.status_code == 200
            assert (await client.post("/api/auth/logout")).status_code == 200
            assert (await client.post("/api/auth/login", json=credentials)).status_code == 200
            assert (await client.post("/api/auth/logout")).status_code == 200
            CHECKPOINT.unlink()
            print(
                "PASS native writes and authentication resume; proof sessions revoked", flush=True
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    steps = parser.add_mutually_exclusive_group(required=True)
    for step in ["prepare", "paused", "released"]:
        steps.add_argument("--" + step, action="store_const", const=step, dest="step")
    asyncio.run(main(parser.parse_args().step))
