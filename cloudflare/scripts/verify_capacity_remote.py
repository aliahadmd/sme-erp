"""Bounded command/report contention measurement on app-staging only.

Run with --run; a prior unfinished checkpoint is never overwritten. --cleanup
reconciles known command receipts and compensates only acknowledged postings.
--new starts another measurement only after the prior run is cleaned. No demo,
production, mail, schema, or recovery-target writes are selectable.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from apply_migrations import literal, query

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
DIRECTORY = ROOT / ".cloudflare/verification/capacity"
CHECKPOINT = DIRECTORY / "measurement.json"
# Targets precede the measurements; nearest-rank p95 is descriptive for these
# small samples. Closed-loop clients are concurrency, not offered requests/sec.
PROFILES = [
    {"name": "baseline", "writers": 1, "posts_each": 8, "lines": 2, "readers": 1, "p95_ms": 5000},
    {"name": "normal", "writers": 4, "posts_each": 5, "lines": 20, "readers": 2, "p95_ms": 5000},
    {"name": "burst", "writers": 8, "posts_each": 3, "lines": 20, "readers": 4, "p95_ms": 10000},
    {"name": "large", "writers": 1, "posts_each": 2, "lines": 100, "readers": 2, "p95_ms": 10000},
]


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


def distribution(values):
    values = sorted(values)
    return {
        "count": len(values),
        **{
            name: round(values[math.ceil(len(values) * quantile) - 1], 1) if values else None
            for name, quantile in [("p50_ms", 0.5), ("p95_ms", 0.95), ("max_ms", 1)]
        },
    }


def summary(profile, samples):
    result = {}
    for kind in ["command", "trial-balance", "ledger", "dashboard"]:
        rows = [row for row in samples if row["kind"] == kind]
        result[kind] = distribution([row["ms"] for row in rows]) | {
            "statuses": dict(Counter(str(row["status"]) for row in rows)),
            "errors": dict(Counter(row["error"] for row in rows if row.get("error"))),
        }
    result["target_passed"] = all(
        row["status"] == (201 if row["kind"] == "command" else 200) and not row.get("error")
        for row in samples
    ) and all(
        result[kind]["count"] > 0 and result[kind]["p95_ms"] <= profile["p95_ms"]
        for kind in ["command", "trial-balance", "ledger", "dashboard"]
    )
    return result


def journal(accounts, memo, count):
    return {
        "memo": memo,
        "lines": [
            {"account_id": accounts[0]["id"], "debit": "0.01"},
            {"account_id": accounts[1]["id"], "credit": "0.01"},
        ]
        * (count // 2),
    }


async def main(step, fresh=False):
    if fresh and CHECKPOINT.exists():
        previous = json.loads(CHECKPOINT.read_text())
        assert previous.get("cleaned"), "Clean the previous measurement before starting another"
        CHECKPOINT.rename(DIRECTORY / (previous["run"] + ".json"))
    proof = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else None
    if step == "run":
        assert proof is None, "Use --cleanup for an unfinished run or --new for a cleaned run"
        proof = {
            "run": uuid.uuid4().hex,
            "url": URL,
            "started_at": datetime.now(UTC).isoformat(),
            "profiles": PROFILES,
            "commands": [],
            "measurements": [],
        }
    assert proof and proof["url"] == URL
    save(proof)
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    prefix = "capacity:" + proof["run"] + ":"
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    ) as client:
        login = await client.post("/api/auth/login", json=credentials)
        assert login.status_code == 200, f"Login: {login.status_code}"
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        if step == "run":
            encoded = login.json()["access_token"].split(".")[1]
            # Persist only the non-secret session identifier, never the token.
            proof["measurement_session_id"] = json.loads(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )["sid"]
            save(proof)

        async def call(method, path, body=None, name=None, expected=200):
            # Setup/cleanup retries keep the same key. Measured requests below
            # deliberately have no automatic retries that could hide failures.
            assert method == "GET" or name
            for attempt in range(3):
                try:
                    response = await client.request(
                        method,
                        path,
                        json=body,
                        headers={"Idempotency-Key": prefix + name} if name else {},
                    )
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
            assert response.status_code == expected, f"{method} {path}: {response.status_code}"
            return response.json() if response.content else None

        async def native(sql):
            return await asyncio.to_thread(query, "app-staging", sql)

        async def books():
            rows = await call("GET", "/api/accounting/trial-balance")
            assert sum(Decimal(row["total_debit"]) for row in rows) == sum(
                Decimal(row["total_credit"]) for row in rows
            )
            return {row["code"]: row["balance"] for row in rows}

        async def counts():
            return await native(
                "SELECT (SELECT COUNT(*) FROM accounting_journal_entries) AS entries,"
                "(SELECT COUNT(*) FROM accounting_journal_lines) AS lines,"
                "(SELECT COUNT(*) FROM cf_commands) AS receipts"
            )

        async def cleanup():
            # Do not POST missing original actions to discover whether they ran.
            # All submitted requests have ended before this step. Ambiguous
            # responses need their durable receipt, or cleanup refuses to guess.
            session_id = str(uuid.UUID(proof["measurement_session_id"]))
            sessions = await native(
                "SELECT user_id,revoked_at FROM platform_sessions WHERE id=" + literal(session_id)
            )
            fenced = (
                len(sessions) == 1
                and sessions[0]["user_id"] == proof["actor_id"]
                and sessions[0]["revoked_at"] is not None
            )
            # Revocation is checked inside every atomic command commit. Once
            # revoked, a delayed original request cannot commit after this read.
            # During a successful run all responses/receipts are already known;
            # an interrupted run must prove this fence before accepting absence.
            identities = {
                command["key"]: hashlib.sha256(
                    json.dumps([ORG, proof["actor_id"], command["key"]]).encode()
                ).hexdigest()
                for command in proof["commands"]
                if command.get("submitted")
            }
            receipts = (
                await native(
                    "SELECT id,response_json FROM cf_commands WHERE id IN ("
                    + ",".join(literal(value) for value in identities.values())
                    + ")"
                )
                if identities
                else []
            )
            receipts = {row["id"]: row["response_json"] for row in receipts}
            originals = []
            for command in proof["commands"]:
                if not command.get("submitted"):
                    continue
                receipt = receipts.get(identities[command["key"]])
                if receipt is None:
                    assert fenced or command.get("status") in {400, 401, 403, 404, 409, 422}, (
                        "Missing ambiguous receipt requires verified original-session revocation"
                    )
                    assert command.get("status") != 201, "A success must have its durable receipt"
                    command["not_committed_after_revocation"] = fenced
                    continue
                result = json.loads(receipt)
                assert result["status"] == 201
                entry = result["body"]
                assert entry["memo"] == command["body"]["memo"]
                assert len(entry["lines"]) == len(command["body"]["lines"])
                assert not command.get("entry_id") or entry["id"] == command["entry_id"]
                command["entry_id"] = entry["id"]
                originals.append(entry)
            proof["reconciled_originals"] = len(originals)
            save(proof)
            for index, entry in enumerate(originals):
                await call(
                    "POST",
                    "/api/accounting/journal-entries",
                    {
                        "memo": "Compensation: " + entry["memo"],
                        "lines": [
                            {
                                "account_id": line["account_id"],
                                "debit": line["credit"],
                                "credit": line["debit"],
                            }
                            for line in entry["lines"]
                        ],
                    },
                    "compensate-" + entry["id"],
                    201,
                )
                if index % 10 == 0:
                    print(f"Reconciled and compensated {index + 1}/{len(originals)}", flush=True)
            for index, account in enumerate(proof.get("accounts", [])):
                await call(
                    "PATCH",
                    "/api/accounting/accounts/" + account["id"],
                    {"is_active": False},
                    "deactivate-" + str(index),
                )
            current = await books()
            assert all(
                Decimal(current.get(code, "0")) == Decimal(proof["baseline"].get(code, "0"))
                for code in current.keys() | proof["baseline"].keys()
            ), "Every account balance must return to its original value"
            assert await call("GET", "/api/reports/dashboard") == proof["dashboard"]
            rows = await native(
                "SELECT e.id,e.number,CAST(l.debit AS TEXT) AS debit,"
                "CAST(l.credit AS TEXT) AS credit FROM accounting_journal_entries e "
                "JOIN accounting_journal_lines l ON l.entry_id=e.id WHERE e.memo LIKE "
                + literal("Capacity " + proof["run"] + "%")
            )
            assert len({row["id"] for row in rows}) == len(originals)
            assert len({row["number"] for row in rows}) == len(originals)
            assert len(rows) == sum(len(entry["lines"]) for entry in originals)
            assert sum(int(row["debit"]) for row in rows) == sum(int(row["credit"]) for row in rows)
            assert not await native("PRAGMA foreign_key_check")
            assert await native("SELECT COUNT(*) AS n FROM cf_assertions") == [{"n": 0}]
            proof.update(
                cleaned=True,
                finished_at=datetime.now(UTC).isoformat(),
                final_counts=await counts(),
                final_health=await call("GET", "/api/operations/health"),
            )
            save(proof)

        try:
            assert (await call("GET", "/api/meta"))["environment"] == "staging"
            assert (await call("GET", "/api/org"))["id"] == ORG
            jobs = await call("GET", "/api/jobs/status")
            assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]
            if step == "cleanup":
                await cleanup()
                print(
                    "PASS: receipts, exact compensation, all balances and foreign keys", flush=True
                )
                return
            proof["actor_id"] = (await call("GET", "/api/auth/me"))["id"]
            proof["baseline"] = await books()
            proof["dashboard"] = await call("GET", "/api/reports/dashboard")
            proof["initial_counts"] = await counts()
            proof["initial_health"] = await call("GET", "/api/operations/health")
            proof["accounts"] = []
            save(proof)
            for index, kind in enumerate(["asset", "equity"]):
                account = await call(
                    "POST",
                    "/api/accounting/accounts",
                    {
                        "code": f"CP{index}" + proof["run"][:12],
                        "name": "Capacity " + proof["run"][:12],
                        "type": kind,
                    },
                    "account-" + str(index),
                    201,
                )
                proof["accounts"].append(account)
                save(proof)

            for profile in proof["profiles"]:
                measurement = {"profile": profile, "samples": []}
                proof["measurements"].append(measurement)
                commands = []
                for index in range(profile["writers"] * profile["posts_each"]):
                    command = {
                        "key": prefix + profile["name"] + ":" + str(index),
                        "body": journal(
                            proof["accounts"],
                            f"Capacity {proof['run']} {profile['name']} {index}",
                            profile["lines"],
                        ),
                    }
                    proof["commands"].append(command)
                    commands.append(command)
                save(proof)
                done = asyncio.Event()

                async def measure(
                    kind, path, command=None, *, profile=profile, measurement=measurement
                ):
                    sample = {"kind": kind, "status": None}
                    started = time.perf_counter()
                    if command:
                        command["submitted"] = True
                        save(proof)
                    try:
                        response = await client.request(
                            "POST" if command else "GET",
                            path,
                            json=command["body"] if command else None,
                            headers={"Idempotency-Key": command["key"]} if command else {},
                        )
                        sample["ms"] = (time.perf_counter() - started) * 1000
                        sample["status"] = response.status_code
                        sample["colo"] = response.headers.get("cf-ray", "").rsplit("-", 1)[-1]
                        body = response.json()
                        if response.status_code != (201 if command else 200):
                            sample["error"] = body.get("error", {}).get("code", "http_error")
                        elif command:
                            command["entry_id"] = body["id"]
                            assert len(body["lines"]) == profile["lines"]
                        elif kind == "trial-balance":
                            assert sum(Decimal(row["total_debit"]) for row in body) == sum(
                                Decimal(row["total_credit"]) for row in body
                            )
                        elif kind == "dashboard":
                            assert body == proof["dashboard"], (
                                "Journal traffic cannot alter invoices"
                            )
                        elif kind == "ledger":
                            balance = Decimal("0")
                            for line in body:
                                balance += Decimal(line["debit"]) - Decimal(line["credit"])
                                assert Decimal(line["balance"]) == balance
                        if command:
                            command["status"] = response.status_code
                    except Exception as exc:
                        sample["ms"] = (time.perf_counter() - started) * 1000
                        sample["error"] = type(exc).__name__
                    measurement["samples"].append(sample)
                    save(proof)

                async def writer(index, *, commands=commands, profile=profile, measure=measure):
                    for command in commands[index :: profile["writers"]]:
                        await measure("command", "/api/accounting/journal-entries", command)

                async def reader(index, *, done=done, measure=measure):
                    paths = [
                        ("trial-balance", "/api/accounting/trial-balance"),
                        (
                            "ledger",
                            "/api/accounting/ledger?account_id=" + proof["accounts"][0]["id"],
                        ),
                        ("dashboard", "/api/reports/dashboard"),
                    ]
                    for number in range(60):
                        await measure(*paths[(number + index) % len(paths)])
                        if number >= 8 and done.is_set():
                            return

                async def writers(*, profile=profile, done=done, writer=writer):
                    try:
                        await asyncio.gather(*(writer(i) for i in range(profile["writers"])))
                    finally:
                        done.set()

                print("Measuring " + profile["name"], flush=True)
                started = time.perf_counter()
                await asyncio.gather(writers(), *(reader(i) for i in range(profile["readers"])))
                measurement["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                measurement["summary"] = summary(profile, measurement["samples"])
                save(proof)
                print(
                    json.dumps({"profile": profile["name"], **measurement["summary"]}), flush=True
                )
                if any(
                    row["kind"] == "command" and (row["status"] != 201 or row.get("error"))
                    for row in measurement["samples"]
                ):
                    raise RuntimeError(
                        "Command failure: remaining load stopped; reconcile receipts"
                    )
            proof["measured_at"] = datetime.now(UTC).isoformat()
            save(proof)
            await cleanup()
            print("PASS: all measured writes reconciled; all original account balances restored")
            if not all(row["summary"]["target_passed"] for row in proof["measurements"]):
                raise RuntimeError("Capacity targets failed; fixture cleanup is complete")
        finally:
            logout = await client.post("/api/auth/logout")
            proof["logout_status"] = logout.status_code
            save(proof)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    steps = parser.add_mutually_exclusive_group(required=True)
    steps.add_argument("--run", action="store_true")
    steps.add_argument("--cleanup", action="store_true")
    parser.add_argument("--new", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main("run" if arguments.run else "cleanup", arguments.new))
