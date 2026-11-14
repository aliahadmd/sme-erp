"""Resumable real journal/adjustment response-loss proofs on app-staging only.

--prepare --scenario selects manual, adjustment-create, adjustment-post or
adjustment-rejected. Open erp-financial at URL/login, then --browser. A changed
key/body is blocked before transmission; cleanup reconciles the known receipt,
adds an exact compensating entry/adjustment and restores all account balances.
--cleanup resumes cleanup after the original process/browser is terminal.
--new --prepare requires a cleaned prior checkpoint. No demo or production writes.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from apply_migrations import literal, query

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
DIRECTORY = ROOT / ".cloudflare/verification/financial-browser"
CHECKPOINT = DIRECTORY / "financial-retry.json"
SCENARIOS = ["manual", "adjustment-create", "adjustment-post", "adjustment-rejected"]


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


def browser(credentials, proof):
    callback = (ROOT / "scripts/browser_financial_retry.js").read_text().strip().removesuffix(";")
    payload = DIRECTORY / "financial-retry-private.js"
    with os.fdopen(os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(
            "async page => ("
            + callback
            + ")(page,"
            + json.dumps(credentials)
            + ","
            + json.dumps(proof)
            + ")"
        )
    try:
        result = subprocess.run(
            [
                "npm",
                "exec",
                "--yes",
                "--package",
                "@playwright/cli@0.1.24",
                "--",
                "playwright-cli",
                "-s=erp-financial",
                "run-code",
                "--filename=" + str(payload),
            ],
            cwd=DIRECTORY,
            capture_output=True,
            text=True,
        )
        text = (result.stdout + result.stderr).replace(credentials["password"], "[redacted]")
        if result.returncode or "### Error" in text:
            raise RuntimeError(
                text.split("### Error", 1)[-1].split("### Ran Playwright code", 1)[0][:1500]
            )
        return json.loads(text.split("### Result\n", 1)[1].split("\n###", 1)[0])
    finally:
        payload.unlink(missing_ok=True)


async def main(step, scenario=None, fresh=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    if fresh and CHECKPOINT.exists():
        prior = json.loads(CHECKPOINT.read_text())
        assert prior.get("cleaned"), "Clean the prior proof before starting another"
        CHECKPOINT.rename(DIRECTORY / ("financial-retry-" + prior["run"] + ".json"))
    proof = (
        json.loads(CHECKPOINT.read_text())
        if CHECKPOINT.exists()
        else {
            "run": uuid.uuid4().hex,
            "url": URL,
            "scenario": scenario,
            "create_key": str(uuid.uuid4()),
            "post_key": str(uuid.uuid4()),
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    assert proof["url"] == URL and proof["scenario"] in SCENARIOS
    assert not scenario or scenario == proof["scenario"], "Scenario differs from the checkpoint"
    assert step == "prepare" or proof.get("prepared"), "Prepare the fixture first"
    save(proof)
    prefix = "browser-financial:" + proof["run"] + ":"
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0"),
    ) as client:
        login = await client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200, f"Login returned {login.status_code}"
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        async def call(method, path, body=None, name=None, expected=200):
            headers = {"Idempotency-Key": prefix + name} if name else {}
            assert method == "GET" or name, "Every fixture mutation needs a stable command key"
            for attempt in range(3):
                try:
                    response = await client.request(method, path, json=body, headers=headers)
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(row["total_debit"]) for row in rows) == sum(
                Decimal(row["total_credit"]) for row in rows
            )
            return {row["code"]: row["balance"] for row in rows}

        async def native(sql):
            return await asyncio.to_thread(query, "app-staging", sql)

        async def cleanup():
            identity = hashlib.sha256(
                json.dumps([ORG, proof["actor_id"], proof["create_key"]]).encode()
            ).hexdigest()
            receipt = await native(
                "SELECT response_json FROM cf_commands WHERE id=" + literal(identity)
            )
            created = json.loads(receipt[0]["response_json"])["body"] if receipt else None
            if proof["scenario"] == "manual":
                rows = await native(
                    "SELECT id FROM accounting_journal_entries WHERE org_id="
                    + literal(ORG)
                    + " AND memo="
                    + literal(proof["memo"])
                )
                assert len(rows) == int(created is not None), (
                    "Exactly one submitted entry is allowed"
                )
                if created:
                    assert rows[0]["id"] == created["id"]
                    a, b = proof["accounts"]
                    await call(
                        "POST",
                        "/api/accounting/journal-entries",
                        {
                            "memo": "Compensation: " + proof["memo"],
                            "lines": [
                                {"account_id": a["id"], "credit": "12.34"},
                                {"account_id": b["id"], "debit": "12.34"},
                            ],
                        },
                        "compensation",
                        201,
                    )
                for n, account in enumerate(proof["accounts"]):
                    await call(
                        "PATCH",
                        f"/api/accounting/accounts/{account['id']}",
                        {"is_active": False},
                        "deactivate-" + str(n),
                    )
                proof["native"] = {
                    "entries": len(rows),
                    "entry_id": created["id"] if created else None,
                }
            else:
                product = proof["product"]
                rows = await native(
                    "SELECT DISTINCT a.id,a.status FROM inventory_adjustments a "
                    "JOIN inventory_adjustment_lines l ON l.adjustment_id=a.id WHERE a.org_id="
                    + literal(ORG)
                    + " AND l.product_id="
                    + literal(product["id"])
                    + " AND l.qty<0"
                )
                assert len(rows) == int(created is not None), (
                    "Exactly one submitted adjustment is allowed"
                )
                moves = []
                if created:
                    assert rows[0]["id"] == created["id"]
                    moves = await native(
                        "SELECT id,CAST(qty AS TEXT) AS qty FROM inventory_stock_moves "
                        "WHERE ref_type='adjustment' AND ref_id=" + literal(created["id"])
                    )
                    assert len(moves) == int(rows[0]["status"] == "posted")
                    if moves:
                        assert moves[0]["qty"] == "-10000"
                        compensation = await call(
                            "POST",
                            "/api/inventory/adjustments",
                            {
                                "reason": "stock_correction",
                                "notes": "Compensation: " + proof["run"],
                                "lines": [
                                    {
                                        "product_id": product["id"],
                                        "qty": "1.0000",
                                        "unit_cost": "2.500000",
                                    }
                                ],
                            },
                            "compensation",
                            201,
                        )
                        await call(
                            "POST",
                            f"/api/inventory/adjustments/{compensation['id']}/post",
                            name="compensation-post",
                        )
                receipt_now = await call("GET", f"/api/inventory/receipts/{proof['receipt']['id']}")
                if receipt_now["status"] == "posted":
                    await call(
                        "POST",
                        f"/api/inventory/receipts/{receipt_now['id']}/void",
                        name="receipt-void",
                    )
                stock = await call("GET", "/api/inventory/stock")
                assert all(
                    Decimal(row["qty_on_hand"]) == 0 and Decimal(row["stock_value"]) == 0
                    for row in stock
                    if row["product_id"] == product["id"]
                )
                await call(
                    "POST",
                    f"/api/catalog/products/{product['id']}/archive",
                    {"archived": True},
                    "archive-product",
                )
                proof["native"] = {
                    "adjustments": len(rows),
                    "movements": len(moves),
                    "adjustment_id": created["id"] if created else None,
                }
            current = await books()
            assert all(
                Decimal(current.get(code, "0")) == Decimal(proof["baseline"].get(code, "0"))
                for code in current.keys() | proof["baseline"].keys()
            ), "Cleanup must restore every account balance"
            proof.update(cleaned=True, finished_at=datetime.now(UTC).isoformat())
            save(proof)

        try:
            assert (await call("GET", "/api/meta"))["environment"] == "staging"
            assert (await call("GET", "/api/org"))["id"] == ORG
            jobs = await call("GET", "/api/jobs/status")
            assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]
            if step == "prepare":
                assert not proof.get("cleaned"), "Use --new --prepare to start another proof"
                proof.setdefault("actor_id", (await call("GET", "/api/auth/me"))["id"])
                proof.setdefault("baseline", await books())
                save(proof)
                if proof["scenario"] == "manual":
                    proof["memo"] = "Browser journal retry " + proof["run"]
                    proof["accounts"] = []
                    for n, kind in enumerate(["asset", "equity"]):
                        account = await call(
                            "POST",
                            "/api/accounting/accounts",
                            {
                                "code": f"BF{n}" + proof["run"][:12],
                                "name": f"Browser retry {n} " + proof["run"][:12],
                                "type": kind,
                            },
                            "account-" + str(n),
                            201,
                        )
                        proof["accounts"].append(account)
                        save(proof)
                else:
                    if proof["scenario"] == "adjustment-rejected":
                        guard = await call("GET", "/api/settings/inventory.guard")
                        assert not guard["value"].get("allow_negative"), (
                            "The rejection proof requires existing negative-stock protection"
                        )
                    proof["product"] = await call(
                        "POST",
                        "/api/catalog/products",
                        {
                            "name": "000 Browser retry " + proof["run"][:12],
                            "sku": "BFR" + proof["run"][:12],
                            "sale_price": "5.00",
                            "cost_price": "2.50",
                        },
                        "product",
                        201,
                    )
                    save(proof)
                    proof["receipt"] = await call(
                        "POST",
                        "/api/inventory/receipts",
                        {
                            "notes": "Browser retry fixture " + proof["run"],
                            "lines": [
                                {
                                    "product_id": proof["product"]["id"],
                                    "qty": "2.0000",
                                    "unit_cost": "2.500000",
                                }
                            ],
                        },
                        "receipt",
                        201,
                    )
                    proof["receipt_post_key"] = prefix + "receipt-post"
                    save(proof)
                    if proof["scenario"] != "adjustment-rejected":
                        await call(
                            "POST",
                            f"/api/inventory/receipts/{proof['receipt']['id']}/post",
                            name="receipt-post",
                        )
                proof["prepared"] = True
                save(proof)
                print("Prepared isolated " + proof["scenario"] + " fixture")
                return
            if step == "cleanup":
                await cleanup()
                print("PASS resumed financial proof cleanup")
                return
            assert not proof.get("cleaned"), "The proof is already cleaned"
            proof["browser_state"] = "running"
            save(proof)
            try:
                proof["browser"] = await asyncio.to_thread(browser, credentials, proof)
                proof["browser_state"] = "complete"
                save(proof)
                print(
                    f"Browser {proof['scenario']}: passed={proof['browser']['passed']}; "
                    "reconciling the native receipt and restoring the fixture",
                    flush=True,
                )
            except BaseException:
                proof["browser_state"] = "unknown"
                save(proof)
                raise
            # Only a returned callback result proves that browser actions ended.
            # A CLI/transport interruption leaves cleanup pending for inspection.
            await cleanup()
            assert proof["browser"]["passed"], (
                "Financial browser regression failed; cleanup completed"
            )
            assert proof["native"].get("entries", proof["native"].get("movements")) == 1
            print(
                "PASS "
                + proof["scenario"]
                + ": one submitted effect; all account balances restored"
            )
        finally:
            logout = await client.post("/api/auth/logout")
            assert logout.status_code == 200, "Proof API logout failed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ["prepare", "browser", "cleanup"]:
        actions.add_argument("--" + name, action="store_const", const=name, dest="step")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--new", action="store_true")
    args = parser.parse_args()
    assert not args.new or args.step == "prepare"
    assert not args.scenario or args.step == "prepare"
    asyncio.run(main(args.step, args.scenario, args.new))
