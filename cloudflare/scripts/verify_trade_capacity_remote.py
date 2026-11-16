"""Measure linked order/stock/invoice/payment contention on app-staging only.

Every mutation is checkpointed before submission. Measurement never retries;
cleanup revokes the original session, reconciles receipts, and reverses only
owned fixtures. --cleanup resumes reconciliation without repeating the workload.
No demo, production, mail, schema or recovery target is selectable.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from apply_migrations import literal, query
from verify_capacity_remote import distribution

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
WAREHOUSE_ID = "01a1de1b-1da8-70e3-802f-dc0c97af2310"
WAREHOUSE_CODE = "TCA9884D3B39BB"
DIRECTORY = ROOT / ".cloudflare/verification/trade-capacity"
CHECKPOINT = DIRECTORY / "measurement.json"
PROFILES = [
    {"name": "baseline", "writers": 1, "cycles_each": 2, "readers": 1, "p95_ms": 5000},
    {"name": "normal", "writers": 4, "cycles_each": 2, "readers": 2, "p95_ms": 5000},
    {"name": "burst", "writers": 8, "cycles_each": 1, "readers": 4, "p95_ms": 10000},
]
KINDS = [
    "order-create",
    "order-confirm",
    "delivery-create",
    "delivery-post",
    "invoice-create",
    "invoice-post",
    "payment-create",
    "trial-balance",
    "stock",
    "dashboard",
]


def start_measurement(kind):
    started = time.perf_counter()
    sample = {
        "kind": kind,
        "status": None,
        "started_at": datetime.now(UTC).isoformat(),
        "transport": [],
    }

    async def trace(name, _info):
        # Trace info includes request headers and response objects. Keep only
        # bounded event labels/times, never info, credentials or message content.
        if len(sample["transport"]) < 64:
            sample["transport"].append(
                {"event": name[:128], "ms": round((time.perf_counter() - started) * 1000, 1)}
            )

    return sample, started, {"trace": trace}


def correlate(sample, response):
    sample["received_at"] = datetime.now(UTC).isoformat()
    sample["http_version"] = response.http_version
    ray = response.headers.get("cf-ray", "")
    if re.fullmatch(r"[0-9a-f]{16,32}-[A-Z]{3}", ray):
        sample["cf_ray"] = ray
    request_id = response.headers.get("x-request-id", "")
    with suppress(ValueError):
        sample["request_id"] = str(uuid.UUID(request_id))


def save(proof):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write(json.dumps(proof, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(CHECKPOINT)


async def native(sql):
    return await asyncio.to_thread(query, "app-staging", sql)


class Experiment:
    def __init__(self, proof, client):
        self.proof = proof
        self.client = client
        self.phase = "setup"
        self.measurement = None

    async def read(self, path):
        response = await self.client.get(path)
        assert response.status_code == 200, f"GET {path}: {response.status_code}"
        return response.json()

    async def login(self):
        assert not self.proof.get("owned_session"), "Revoke the saved owned session first"
        credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
        response = await self.client.post("/api/auth/login", json=credentials)
        assert response.status_code == 200, f"Login: {response.status_code}"
        token = response.json()["access_token"]
        encoded = token.split(".")[1]
        sid = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["sid"]
        self.proof["owned_session"] = {
            "id": str(uuid.UUID(sid)),
            "cookies": [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.client.cookies.jar
            ],
        }
        save(self.proof)
        self.client.headers["Authorization"] = "Bearer " + token
        actor = await self.read("/api/auth/me")
        assert actor["is_superuser"]
        assert self.proof.get("actor_id", actor["id"]) == actor["id"]
        self.proof["actor_id"] = actor["id"]
        save(self.proof)
        return sid

    async def logout(self):
        session = self.proof.get("owned_session")
        if not session:
            return
        self.client.cookies.clear()
        for cookie in session["cookies"]:
            self.client.cookies.set(**cookie)
        response = await self.client.post("/api/auth/logout")
        assert response.status_code == 200, f"Owned logout: {response.status_code}"
        rows = await native(
            "SELECT revoked_at FROM platform_sessions WHERE id=" + literal(session["id"])
        )
        assert len(rows) == 1 and rows[0]["revoked_at"] is not None
        self.proof.setdefault("revoked_sessions", []).append(session["id"])
        self.proof.pop("owned_session")
        self.client.headers.pop("Authorization", None)
        self.client.cookies.clear()
        save(self.proof)

    async def command(self, name, kind, path, body=None, *, expected=200, method="POST"):
        record = next((x for x in self.proof["commands"] if x["name"] == name), None)
        data = {"name": name, "kind": kind, "path": path, "body": body, "method": method}
        if record:
            assert all(record[k] == v for k, v in data.items()), "Immutable request changed"
        else:
            record = {**data, "phase": self.phase, "key": f"trade:{self.proof['run']}:{name}"}
            self.proof["commands"].append(record)
        record["submitted"] = True
        save(self.proof)
        sample, started, extensions = start_measurement(kind)
        try:
            for attempt in range(1 if self.phase == "measure" else 3):
                try:
                    response = await self.client.request(
                        method,
                        path,
                        json=body,
                        headers={"Idempotency-Key": record["key"]},
                        extensions=extensions if self.phase == "measure" else {},
                    )
                    break
                except httpx.TransportError:
                    if self.phase == "measure" or attempt == 2:
                        raise
            sample["status"] = record["status"] = response.status_code
            correlate(sample, response)
            assert response.status_code == expected, f"{kind}: HTTP {response.status_code}"
            result = response.json() if response.content else None
            record["result"] = result
            return result
        except Exception as exc:
            sample["error"] = type(exc).__name__
            raise
        finally:
            sample["ms"] = round((time.perf_counter() - started) * 1000, 1)
            if self.phase == "measure":
                self.measurement["samples"].append(sample)
            save(self.proof)

    async def counts(self):
        return await native(
            "SELECT (SELECT COUNT(*) FROM accounting_journal_entries) AS journals,"
            "(SELECT COUNT(*) FROM accounting_journal_lines) AS journal_lines,"
            "(SELECT COUNT(*) FROM invoicing_invoices) AS invoices,"
            "(SELECT COUNT(*) FROM invoicing_payments) AS payments,"
            "(SELECT COUNT(*) FROM inventory_stock_moves) AS stock_moves,"
            "(SELECT COUNT(*) FROM cf_commands) AS receipts"
        )

    async def setup(self):
        self.proof.update(
            baseline=await self.books(),
            dashboard=await self.read("/api/reports/dashboard"),
            initial_counts=await self.counts(),
        )
        assert (await self.read("/api/operations/health"))["status"] == "ok"
        save(self.proof)
        warehouse = next(
            (w for w in await self.read("/api/inventory/warehouses") if w["id"] == WAREHOUSE_ID),
            None,
        )
        assert warehouse and warehouse["code"] == WAREHOUSE_CODE
        assert await self.read("/api/inventory/stock?warehouse_id=" + warehouse["id"]) == []
        self.proof["warehouse"] = warehouse
        save(self.proof)
        await self.command(
            "setup:party",
            "party",
            "/api/crm/contacts",
            {"name": "Trade capacity " + self.proof["run"], "is_customer": True},
            expected=201,
        )
        products = []
        for i in range(3):
            products.append(
                await self.command(
                    f"setup:product:{i}",
                    "product",
                    "/api/catalog/products",
                    {
                        "name": f"Trade {self.proof['run']} {i}",
                        "sale_price": "20",
                        "cost_price": "10",
                    },
                    expected=201,
                )
            )
        supply = sum(p["writers"] * p["cycles_each"] for p in self.proof["profiles"]) + 2
        self.proof["supply_each"] = supply
        receipt = await self.command(
            "setup:receipt",
            "receipt",
            "/api/inventory/receipts",
            {
                "warehouse_id": warehouse["id"],
                "notes": "Trade capacity " + self.proof["run"],
                "lines": [
                    {"product_id": p["id"], "qty": str(supply), "unit_cost": "10"} for p in products
                ],
            },
            expected=201,
        )
        await self.command(
            "setup:receipt-post", "receipt-post", f"/api/inventory/receipts/{receipt['id']}/post"
        )

    def summarize(self, profile, measurement):
        samples = measurement["samples"]
        result = {
            kind: distribution([s["ms"] for s in samples if s["kind"] == kind]) for kind in KINDS
        }
        result["cycle"] = distribution(measurement["cycles_ms"])
        result["target_passed"] = (
            not measurement["writer_errors"]
            and all(s["status"] in {200, 201} and not s.get("error") for s in samples)
            and all(result[k]["count"] and result[k]["p95_ms"] <= profile["p95_ms"] for k in KINDS)
        )
        return result

    async def measure(self, profile):
        self.phase = "measure"
        measurement = {"profile": profile, "samples": [], "cycles_ms": [], "writer_errors": []}
        self.measurement = measurement
        self.proof["measurements"].append(measurement)
        save(self.proof)
        failed, done = asyncio.Event(), asyncio.Event()
        party = self.entity("setup:party")
        warehouse = self.entity("setup:warehouse")
        products = [self.entity(f"setup:product:{i}") for i in range(3)]

        async def cycle(name):
            started = time.perf_counter()

            async def action(step, path, body=None, expected=200):
                if failed.is_set():
                    raise RuntimeError("Stopped before next command after a peer failure")
                return await self.command(name + ":" + step, step, path, body, expected=expected)

            order = await action(
                "order-create",
                "/api/sales/orders",
                {
                    "customer_id": party["id"],
                    "notes": "Trade capacity " + self.proof["run"],
                    "lines": [
                        {"product_id": p["id"], "qty": "1", "unit_price": "20"} for p in products
                    ],
                },
                201,
            )
            await action("order-confirm", f"/api/sales/orders/{order['id']}/confirm")
            delivery = await action(
                "delivery-create",
                "/api/inventory/deliveries",
                {
                    "warehouse_id": warehouse["id"],
                    "source_so_id": order["id"],
                },
                201,
            )
            await action("delivery-post", f"/api/inventory/deliveries/{delivery['id']}/post")
            invoice = await action(
                "invoice-create",
                "/api/invoicing/invoices",
                {
                    "invoice_type": "ar",
                    "party_id": party["id"],
                    "source_order_id": order["id"],
                },
                201,
            )
            posted = await action("invoice-post", f"/api/invoicing/invoices/{invoice['id']}/post")
            assert posted["total"] == "60.00" and len(posted["lines"]) == 3
            payment = await action(
                "payment-create",
                "/api/invoicing/payments",
                {
                    "party_id": party["id"],
                    "direction": "in",
                    "amount": "60.00",
                    "method": "bank",
                    "allocations": [{"invoice_id": invoice["id"], "amount": "60.00"}],
                },
                201,
            )
            assert payment["amount"] == "60.00" and Decimal(payment["unallocated"]) == 0
            measurement["cycles_ms"].append(round((time.perf_counter() - started) * 1000, 1))
            save(self.proof)

        async def writer(index):
            for number in range(profile["cycles_each"]):
                if failed.is_set():
                    return
                try:
                    await cycle(f"{profile['name']}:{index}:{number}")
                except Exception as exc:
                    failed.set()
                    measurement["writer_errors"].append(type(exc).__name__ + ": " + str(exc))
                    save(self.proof)
                    return

        async def reader(index):
            paths = [
                ("trial-balance", "/api/accounting/trial-balance"),
                ("stock", "/api/inventory/stock?warehouse_id=" + warehouse["id"]),
                ("dashboard", "/api/reports/dashboard"),
            ]
            for number in range(120):
                kind, path = paths[(number + index) % len(paths)]
                sample, started, extensions = start_measurement(kind)
                try:
                    response = await self.client.get(path, extensions=extensions)
                    sample["status"] = response.status_code
                    correlate(sample, response)
                    if response.status_code != 200:
                        try:
                            sample["error_code"] = str(
                                response.json().get("error", {}).get("code", "http_error")
                            )[:64]
                        except ValueError:
                            sample["error_code"] = "non_json_response"
                    assert response.status_code == 200
                    body = response.json()
                    if kind == "trial-balance":
                        assert sum(Decimal(r["total_debit"]) for r in body) == sum(
                            Decimal(r["total_credit"]) for r in body
                        )
                    elif kind == "stock":
                        assert {r["product_id"] for r in body} == {p["id"] for p in products}
                        assert len({r["qty_on_hand"] for r in body}) == 1
                        for row in body:
                            assert 0 <= Decimal(row["qty_on_hand"]) <= self.proof["supply_each"]
                            assert Decimal(row["avg_cost"]) == 10
                            assert Decimal(row["stock_value"]) == Decimal(row["qty_on_hand"]) * 10
                    else:
                        assert Decimal(body["open_ar"]) >= Decimal(
                            self.proof["dashboard"]["open_ar"]
                        )
                except Exception as exc:
                    sample["error"] = type(exc).__name__
                sample["ms"] = round((time.perf_counter() - started) * 1000, 1)
                measurement["samples"].append(sample)
                save(self.proof)
                if number >= 8 and done.is_set():
                    return

        writer_tasks = [asyncio.create_task(writer(i)) for i in range(profile["writers"])]

        async def writers():
            try:
                await asyncio.gather(*writer_tasks)
            finally:
                done.set()

        print("Measuring linked trade " + profile["name"], flush=True)
        tasks = [
            asyncio.create_task(writers()),
            *(asyncio.create_task(reader(i)) for i in range(profile["readers"])),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            # A reader/setup exception must not leave writers running beyond
            # this experiment (or beyond a local test's isolated bindings).
            for task in [*tasks, *writer_tasks]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, *writer_tasks, return_exceptions=True)
        measurement["summary"] = self.summarize(profile, measurement)
        save(self.proof)
        print(json.dumps({"profile": profile["name"], **measurement["summary"]}), flush=True)
        return not failed.is_set()

    def entity(self, name):
        if name == "setup:warehouse" and self.proof.get("warehouse"):
            return self.proof["warehouse"]
        return next((c.get("result") for c in self.proof["commands"] if c["name"] == name), None)

    async def books(self):
        rows = await self.read("/api/accounting/trial-balance")
        assert sum(Decimal(r["total_debit"]) for r in rows) == sum(
            Decimal(r["total_credit"]) for r in rows
        )
        return {r["code"]: r["balance"] for r in rows}

    async def guard(self):
        assert (await self.read("/api/meta"))["environment"] == "staging"
        assert (await self.read("/api/org"))["id"] == ORG
        jobs = await self.read("/api/jobs/status")
        assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]

    async def reconcile(self):
        session = str(uuid.UUID(self.proof["measurement_session"]))
        rows = await native(
            "SELECT user_id,revoked_at FROM platform_sessions WHERE id=" + literal(session)
        )
        assert len(rows) == 1 and rows[0]["user_id"] == self.proof["actor_id"]
        assert rows[0]["revoked_at"] is not None, "Original session must be fenced before cleanup"
        records = [c for c in self.proof["commands"] if c["phase"] != "cleanup"]
        for offset in range(0, len(records), 40):
            chunk = records[offset : offset + 40]
            ids = {
                hashlib.sha256(
                    json.dumps([ORG, self.proof["actor_id"], c["key"]]).encode()
                ).hexdigest(): c
                for c in chunk
                if c.get("submitted")
            }
            receipts = (
                await native(
                    "SELECT id,response_json FROM cf_commands WHERE id IN ("
                    + ",".join(literal(i) for i in ids)
                    + ")"
                )
                if ids
                else []
            )
            found = {r["id"]: json.loads(r["response_json"]) for r in receipts}
            for identity, command in ids.items():
                if identity not in found:
                    assert command.get("status") not in {200, 201}, "Success receipt missing"
                    command["absent_after_revocation"] = True
                    continue
                receipt = found[identity]
                assert receipt["status"] == command.get("status", receipt["status"])
                assert command.get("result", receipt["body"]) == receipt["body"]
                command["result"] = receipt["body"]
                command["receipt_verified"] = True
            save(self.proof)

    async def cleanup(self):
        self.phase = "cleanup"
        await self.reconcile()
        # Receipt-owned IDs are the only entities eligible for compensation.
        for suffix, path in [
            (":payment-create", "/api/invoicing/payments"),
            (":invoice-create", "/api/invoicing/invoices"),
            (":delivery-create", "/api/inventory/deliveries"),
            (":order-create", "/api/sales/orders"),
        ]:
            entities = [
                c["result"]
                for c in self.proof["commands"]
                if c["name"].endswith(suffix) and c.get("receipt_verified")
            ]
            for entity in entities:
                if suffix == ":payment-create":
                    # The original payment API has no single-payment GET.
                    rows = await native(
                        "SELECT id,party_id,status FROM invoicing_payments WHERE org_id="
                        + literal(ORG)
                        + " AND id="
                        + literal(entity["id"])
                    )
                    assert len(rows) == 1
                    row = rows[0]
                else:
                    row = await self.read(path + "/" + entity["id"])
                if suffix == ":delivery-create":
                    assert row["warehouse_id"] == self.entity("setup:warehouse")["id"]
                else:
                    party_field = "customer_id" if suffix == ":order-create" else "party_id"
                    assert row[party_field] == self.entity("setup:party")["id"]
                status = row["status"]
                if status in {"void", "cancelled"}:
                    continue
                if status == "draft" and suffix != ":order-create":
                    continue
                action = "cancel" if suffix == ":order-create" else "void"
                await self.command(
                    "cleanup:" + action + ":" + entity["id"],
                    action,
                    path + "/" + entity["id"] + "/" + action,
                )
        receipt = self.entity("setup:receipt")
        warehouse = self.entity("setup:warehouse")
        if receipt:
            row = await self.read("/api/inventory/receipts/" + receipt["id"])
            assert row["warehouse_id"] == warehouse["id"]
            if row["status"] == "posted":
                await self.command(
                    "cleanup:supply-void",
                    "receipt-void",
                    f"/api/inventory/receipts/{receipt['id']}/void",
                )
        if warehouse:
            assert await self.read("/api/inventory/stock?warehouse_id=" + warehouse["id"]) == []
        current = await self.books()
        baseline = self.proof["baseline"]
        assert all(
            Decimal(current.get(k, "0")) == Decimal(baseline.get(k, "0"))
            for k in current.keys() | baseline.keys()
        ), "Original account balances changed"
        expected_dashboard = dict(self.proof["dashboard"])
        # The first verifier created an immutable-history warehouse. Its
        # low-stock effect is derived independently, never from the observed
        # dashboard difference. Later runs reuse that admitted empty warehouse.
        if any(
            c["name"] == "setup:warehouse" and c.get("receipt_verified")
            for c in self.proof["commands"]
        ):
            low = await native(
                "SELECT COUNT(*) AS n FROM catalog_products WHERE org_id="
                + literal(ORG)
                + " AND status='active' AND type='goods' AND track_inventory=1 AND min_stock>0"
            )
            delta = low[0]["n"]
            expected_dashboard["low_stock_count"] += delta
            self.proof["retained_warehouse_effect"] = {
                "id": warehouse["id"],
                "low_stock_count_delta": delta,
            }
            save(self.proof)
        assert await self.read("/api/reports/dashboard") == expected_dashboard
        for name, path in [
            ("setup:party", "/api/crm/contacts"),
            *[(f"setup:product:{i}", "/api/catalog/products") for i in range(3)],
        ]:
            entity = self.entity(name)
            if entity:
                await self.command(
                    "cleanup:archive:" + entity["id"],
                    "archive",
                    path + "/" + entity["id"] + "/archive",
                    {"archived": True},
                )
        assert not await native("PRAGMA foreign_key_check")
        assert await native("SELECT COUNT(*) AS n FROM cf_assertions") == [{"n": 0}]
        for table in [
            "accounting_journal_entries",
            "invoicing_invoices",
            "invoicing_payments",
            "sales_sales_orders",
            "inventory_deliveries",
        ]:
            assert not await native(
                f"SELECT number FROM {table} WHERE number IS NOT NULL "
                "GROUP BY org_id,number HAVING COUNT(*)>1"
            ), "Duplicate document number in " + table
        self.proof.update(
            cleaned=True,
            finished_at=datetime.now(UTC).isoformat(),
            final_counts=await self.counts(),
            final_health=await self.read("/api/operations/health"),
        )
        save(self.proof)
        print(
            "PASS: trades reversed; original books, zero fixture stock and expected dashboard",
            flush=True,
        )

    async def verify_completed(self):
        orders = [
            c["result"]
            for c in self.proof["commands"]
            if c["kind"] == "order-create" and c.get("result")
        ]
        for order in orders:
            row = await self.read("/api/sales/orders/" + order["id"])
            assert row["status"] == "closed" and len(row["lines"]) == 3
            assert all(
                Decimal(line["qty_delivered"]) == Decimal(line["qty_invoiced"]) == 1
                for line in row["lines"]
            )
        invoices = [
            c["result"]
            for c in self.proof["commands"]
            if c["kind"] == "invoice-create" and c.get("result")
        ]
        for invoice in invoices:
            row = await self.read("/api/invoicing/invoices/" + invoice["id"])
            assert row["status"] == "paid" and Decimal(row["open_balance"]) == 0
            assert row["total"] == row["amount_paid"] == "60.00"
        stock = await self.read(
            "/api/inventory/stock?warehouse_id=" + self.entity("setup:warehouse")["id"]
        )
        assert len(stock) == 3
        assert all(
            Decimal(r["qty_on_hand"]) == self.proof["supply_each"] - len(orders) for r in stock
        )
        books = await self.books()
        for code, delta in {
            "1100": 0,
            "1200": 30 * (self.proof["supply_each"] - len(orders)),
            "5000": 30 * len(orders),
            "4000": -60 * len(orders),
        }.items():
            assert Decimal(books[code]) == Decimal(self.proof["baseline"][code]) + delta, code
        self.proof["completed_workflows_verified"] = len(orders)
        save(self.proof)


async def main(step, fresh=False):
    if fresh and CHECKPOINT.exists():
        prior = json.loads(CHECKPOINT.read_text())
        assert prior.get("cleaned") and not prior.get("owned_session")
        CHECKPOINT.rename(DIRECTORY / (prior["run"] + ".json"))
    proof = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else None
    if step == "run":
        assert proof is None, "Use --cleanup for unfinished work or --new after cleanup"
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
    async with httpx.AsyncClient(
        base_url=URL,
        timeout=180,
        headers={"Origin": URL},
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    ) as client:
        experiment = Experiment(proof, client)
        await experiment.logout()  # Resume only the saved owned session's cleanup.
        sid = await experiment.login()
        try:
            await experiment.guard()
            if step == "cleanup":
                await experiment.cleanup()
                return
            proof["measurement_session"] = sid
            save(proof)
            try:
                await experiment.setup()
                for profile in proof["profiles"]:
                    if not await experiment.measure(profile):
                        raise RuntimeError("Command failure stopped further load")
                await experiment.verify_completed()
            except Exception as exc:
                proof["measurement_error"] = type(exc).__name__ + ": " + str(exc)
                save(proof)
            finally:
                await experiment.logout()
            await experiment.login()
            await experiment.guard()
            await experiment.cleanup()
            assert not proof.get("measurement_error"), "Measurement failed; cleanup completed"
            assert all(m["summary"]["target_passed"] for m in proof["measurements"]), (
                "Capacity targets failed; cleanup completed"
            )
        finally:
            await experiment.logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    steps = parser.add_mutually_exclusive_group(required=True)
    steps.add_argument("--run", action="store_true")
    steps.add_argument("--cleanup", action="store_true")
    parser.add_argument("--new", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main("run" if arguments.run else "cleanup", arguments.new))
