"""Prepare/run/clean a resumable lost-payment-response proof on app-staging only.

Run --prepare, open the named erp-financial Playwright session at URL/login,
then run --browser. The browser transmits a real payment, loses its committed
response and retries through the UI. Native receipts/rows are checked, then
the test payment is voided and the test contact archived, even after failure.
--cleanup resumes that cleanup after an interrupted process; it never creates
a payment to discover its outcome. --new --prepare requires a cleaned prior run.
No production target, demo financial data, real mail or database reset is used.
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
CHECKPOINT = DIRECTORY / "payment-retry.json"


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


def browser(credentials, proof):
    script = (ROOT / "scripts/browser_payment_retry.js").read_text().strip().removesuffix(";")
    source = (
        "async page => ("
        + script
        + ")(page,"
        + json.dumps(credentials)
        + ","
        + json.dumps(proof)
        + ")"
    )
    payload = DIRECTORY / "payment-retry-private.js"
    with os.fdopen(os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(source)
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
            timeout=240,
        )
        text = (result.stdout + result.stderr).replace(credentials["password"], "[redacted]")
        if result.returncode or "### Error" in text:
            raise RuntimeError(
                text.split("### Error", 1)[-1].split("### Ran Playwright code", 1)[0][:1500]
            )
        return json.loads(text.split("### Result\n", 1)[1].split("\n###", 1)[0])
    finally:
        payload.unlink(missing_ok=True)


async def main(step, fresh=False, restart=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    if fresh and CHECKPOINT.exists():
        prior = json.loads(CHECKPOINT.read_text())
        assert prior.get("cleaned"), "Clean the previous financial proof before starting another"
        CHECKPOINT.rename(DIRECTORY / ("payment-retry-" + prior["run"] + ".json"))
    proof = (
        json.loads(CHECKPOINT.read_text())
        if CHECKPOINT.exists()
        else {
            "run": uuid.uuid4().hex,
            "payment_key": str(uuid.uuid4()),
            "url": URL,
            "started_at": datetime.now(UTC).isoformat(),
            "restart": restart,
        }
    )
    assert proof["url"] == URL
    assert step == "prepare" or "party_id" in proof, "Prepare the proof first"
    save(proof)
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        transport=httpx.AsyncHTTPTransport(retries=3, local_address="0.0.0.0"),
    ) as client:
        login = await client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200, f"Login returned {login.status_code}"
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        async def call(method, path, body=None, key=None, expected=200):
            response = await client.request(
                method, path, json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex}
            )
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(row["total_debit"]) for row in rows) == sum(
                Decimal(row["total_credit"]) for row in rows
            )
            return {row["code"]: row["balance"] for row in rows}

        async def cleanup():
            identity = hashlib.sha256(
                json.dumps([ORG, proof["actor_id"], proof["payment_key"]]).encode()
            ).hexdigest()
            rows = await asyncio.to_thread(
                query,
                "app-staging",
                "SELECT response_json FROM cf_commands WHERE id=" + literal(identity),
            )
            if rows:
                payment = json.loads(rows[0]["response_json"])["body"]
                proof["payment_id"] = payment["id"]
                save(proof)
                native = await asyncio.to_thread(
                    query,
                    "app-staging",
                    "SELECT count(*) AS payments, CAST(sum(amount) AS TEXT) AS cents "
                    "FROM invoicing_payments "
                    f"WHERE org_id={literal(ORG)} AND party_id={literal(proof['party_id'])}",
                )
                assert native == [{"payments": 1, "cents": "1234"}], (
                    "The proof must have exactly one native payment"
                )
                journals = await asyncio.to_thread(
                    query,
                    "app-staging",
                    "SELECT count(*) AS postings FROM accounting_journal_entries "
                    "WHERE source_type='payment' AND source_id=" + literal(payment["id"]),
                )
                assert journals == [{"postings": 1}], "The payment must have one original posting"
                voided = await call(
                    "POST",
                    f"/api/invoicing/payments/{payment['id']}/void",
                    key="browser-payment:" + proof["run"] + ":void",
                )
                assert voided["status"] == "void"
                reversals = await asyncio.to_thread(
                    query,
                    "app-staging",
                    "SELECT count(*) AS reversals FROM accounting_journal_entries "
                    "WHERE source_type='reversal' AND source_id=" + literal(payment["id"]),
                )
                assert reversals == [{"reversals": 1}], "The proof must have one reversal"
                proof["native"] = {
                    "payments": 1,
                    "original_postings": 1,
                    "reversals": 1,
                    "amount": "12.34",
                    "voided": True,
                }
            await call(
                "POST",
                f"/api/crm/contacts/{proof['party_id']}/archive",
                {"archived": True},
                key="browser-payment:" + proof["run"] + ":archive",
            )
            current = await books()
            assert all(
                Decimal(current.get(code, "0")) == Decimal(proof["baseline"].get(code, "0"))
                for code in current.keys() | proof["baseline"].keys()
            ), "Cleanup must restore every account balance"
            proof["cleaned"] = True
            proof["finished_at"] = datetime.now(UTC).isoformat()
            save(proof)

        try:
            assert (await call("GET", "/api/meta"))["environment"] == "staging"
            assert (await call("GET", "/api/org"))["id"] == ORG
            jobs = await call("GET", "/api/jobs/status")
            assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]
            if step == "prepare":
                assert not proof.get("cleaned"), "Use --new --prepare for a new proof"
                proof.setdefault("actor_id", (await call("GET", "/api/auth/me"))["id"])
                proof.setdefault("baseline", await books())
                save(proof)
                party = await call(
                    "POST",
                    "/api/crm/contacts",
                    {"name": "Browser payment retry " + proof["run"][:10], "is_customer": True},
                    key="browser-payment:" + proof["run"] + ":party",
                    expected=201,
                )
                proof.update(party_id=party["id"], party_name=party["name"])
                save(proof)
                print("Prepared isolated browser payment fixture; no payment has been submitted")
                return
            if step == "cleanup":
                await cleanup()
                print("PASS financial proof cleanup; account balances restored")
                return
            assert not proof.get("cleaned"), "This proof has already been cleaned"
            proof["browser_state"] = "running"
            save(proof)
            try:
                proof["browser"] = await asyncio.to_thread(browser, credentials, proof)
                proof["browser_state"] = "complete"
                save(proof)
            except BaseException:
                proof["browser_state"] = "failed"
                save(proof)
                raise
            finally:
                await cleanup()
            assert proof["browser"]["passed"], (
                "Browser payment retry regression failed; fixture cleanup completed"
            )
            print(
                "PASS real committed payment response loss/retry: "
                "one payment, one original posting, exact reversal"
            )
        finally:
            response = await client.post("/api/auth/logout")
            assert response.status_code == 200, "Financial proof API logout failed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ["prepare", "browser", "cleanup"]:
        actions.add_argument("--" + name, action="store_const", const=name, dest="step")
    parser.add_argument(
        "--new", action="store_true", help="Archive a cleaned prior checkpoint before preparation"
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Prepare a proof that resets the coordinator after commit",
    )
    args = parser.parse_args()
    assert not args.new or args.step == "prepare", "--new requires --prepare"
    assert not args.restart or args.step == "prepare", "--restart requires --prepare"
    asyncio.run(main(args.step, args.new, args.restart))
