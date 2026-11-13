"""Exercise the real staging Worker/D1 accounting boundary with synthetic books.

Creates dedicated proof accounts and immutable journal records in isolated
staging. Credentials are read privately and are never printed. Optional failure
proof requires fixtures/accounting_fault_probe.sql on isolated staging. Remove
that temporary trigger after the check; never apply the fixture to production.
"""

import argparse
import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
FAILURE_MEMO = "Cloudflare accounting atomic rollback probe"


async def main(failure_probe=False):
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    run = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=120) as client:
        response = await client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert response.status_code == 200, f"Login returned {response.status_code}"
        authorization = {"Authorization": "Bearer " + response.json()["access_token"]}

        async def call(method, path, *, body=None, key=None, expected=200, params=None):
            headers = dict(authorization)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            for attempt in range(3):
                try:
                    result = await client.request(
                        method, path, headers=headers, json=body, params=params
                    )
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            assert result.status_code == expected, (
                f"{method} {path}: expected {expected}, got {result.status_code}; "
                + result.text[:600]
            )
            return result.json()

        accounts = []
        for code, kind in [("D", "asset"), ("C", "equity")]:
            accounts.append(
                await call(
                    "POST",
                    "/api/accounting/accounts",
                    body={
                        "code": f"CF{code}{run}",
                        "name": f"Migration proof {code}",
                        "type": kind,
                    },
                    expected=201,
                )
            )
        debit, credit = accounts

        def body(amount, memo, *, count=1, day="2026-01-02"):
            return {
                "memo": memo,
                "entry_date": day,
                "lines": [
                    {"account_id": debit["id"], "debit": amount},
                    {"account_id": credit["id"], "credit": amount},
                ]
                * count,
            }

        async def balance():
            rows = await call("GET", "/api/accounting/trial-balance")
            return Decimal(next(r for r in rows if r["account_id"] == debit["id"])["balance"])

        if failure_probe:
            before = await call("GET", "/api/accounting/journal-entries", params={"limit": 1})
            key = f"rollback-{run}"
            await call(
                "POST",
                "/api/accounting/journal-entries",
                body=body("1", FAILURE_MEMO),
                key=key,
                expected=409,
            )
            after = await call("GET", "/api/accounting/journal-entries", params={"limit": 1})
            assert before["total"] == after["total"] and await balance() == 0
            # A rejected batch must leave no receipt claiming this key.
            await call(
                "POST",
                "/api/accounting/journal-entries",
                body=body("1", "Rollback retry"),
                key=key,
                expected=201,
            )
            assert await balance() == Decimal("1.00")
            print("PASS: real D1 failure rolls back journals and receipts; retry commits")

        initial = await balance()
        maximum = "9999999999999999.99"
        request = body(maximum, f"Exact-value proof {run}", count=10)
        duplicate_key = f"exact-{run}"
        results = await asyncio.gather(
            *[
                call(
                    "POST",
                    "/api/accounting/journal-entries",
                    body=request,
                    key=duplicate_key,
                    expected=201,
                )
                for _ in range(6)
            ]
        )
        assert len({r["id"] for r in results}) == 1
        assert len(results[0]["lines"]) == 20
        assert results[0]["lines"][0]["debit"] == maximum
        expected = initial + Decimal(maximum) * 10
        assert await balance() == expected
        await call(
            "POST",
            "/api/accounting/journal-entries",
            body=body("2", "Changed payload"),
            key=duplicate_key,
            expected=409,
        )
        print("PASS: concurrent replay commits once; exact totals exceed SQLite SUM range")

        rounded = await call(
            "POST",
            "/api/accounting/journal-entries",
            body=body("10.125", f"Rounding proof {run}", day="2026-01-03"),
            expected=201,
        )
        assert rounded["lines"][0]["debit"] == "10.12"
        expected += Decimal("10.12")
        # Cross the reader's 500-row boundary through the actual Worker batch.
        large = await call(
            "POST",
            "/api/accounting/journal-entries",
            body=body("1", f"Large journal proof {run}", count=300, day="2026-01-04"),
            expected=201,
        )
        assert len(large["lines"]) == 600
        expected += Decimal("300.00")
        assert await balance() == expected
        ledger = await call("GET", "/api/accounting/ledger", params={"account_id": debit["id"]})
        assert len(ledger) == 311 + int(failure_probe)
        assert Decimal(ledger[-1]["balance"]) == expected
        early = await call("GET", "/api/accounting/trial-balance", params={"as_of": "2026-01-02"})
        assert Decimal(next(r for r in early if r["account_id"] == debit["id"])["balance"]) == (
            initial + Decimal(maximum) * 10
        )
        print("PASS: 600-line atomic post, half-even rounding, dated and paged exact reports")

        page = await call(
            "GET", "/api/accounting/journal-entries", params={"source_type": "manual", "limit": 100}
        )
        stored = next(r for r in page["items"] if r["id"] == results[0]["id"])
        assert len(stored["lines"]) == 20 and stored["lines"][0]["debit"] == maximum
        assert stored["lines"][0]["account_code"] == debit["code"]
        invalid = body("1", "Unbalanced")
        invalid["lines"][1]["credit"] = "2"
        await call("POST", "/api/accounting/journal-entries", body=invalid, expected=422)
        await call("PATCH", f"/api/accounting/accounts/{debit['id']}", body={"is_active": False})
        await call(
            "POST", "/api/accounting/journal-entries", body=body("1", "Inactive"), expected=422
        )
        assert await balance() == expected
        print("PASS: stored journal detail, account labels and invalid/inactive posting guards")
        await client.post("/api/auth/logout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--failure-probe", action="store_true")
    asyncio.run(main(parser.parse_args().failure_probe))
