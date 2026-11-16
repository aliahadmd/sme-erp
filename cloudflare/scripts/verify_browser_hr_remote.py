"""Resumable HR browser acceptance on app-staging; no real mail is sent.

Prepare two isolated actors and a leave type, run the real Chrome workflow,
then fence those actors and cancel their pending requests. Approved/rejected
synthetic history stays; the coherent demo and production are not selectable.
"""

import argparse
import asyncio
import base64
import fcntl
import json
import os
import secrets
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".cloudflare/verification/hr-browser"
CHECKPOINT = DIRECTORY / "workflow.json"
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
ORG = "019a78e1-6d80-7000-8000-000000000001"
KEYS = ("submitA", "approveA", "submitB", "rejectB", "submitC", "cancelC", "submitD")


@contextmanager
def owned_run():
    """Keep prepare/browser/cleanup mutually exclusive, including across processes."""
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(DIRECTORY / "run.lock", os.O_RDWR | os.O_CREAT, 0o600), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "An HR verification step is still running; wait for it to finish"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        json.dump(value, out, indent=2)
    temporary.replace(path)


def browser_command(*args):
    return subprocess.run(
        [
            "npm",
            "exec",
            "--yes",
            "--package",
            "@playwright/cli@0.1.24",
            "--",
            "playwright-cli",
            "-s=erp-hr",
            *args,
        ],
        cwd=DIRECTORY,
        capture_output=True,
        text=True,
        timeout=600,
    )


def close_browser():
    result = browser_command("close")
    if result.returncode or "### Error" in result.stdout:
        raise RuntimeError("Could not close the owned HR browser; inspect it before cleanup")


def run_browser(proof):
    assert proof.get("prepared") and not proof.get("cleaned")
    payload = DIRECTORY / "workflow-private.js"
    evidence = DIRECTORY / "browser.json"
    assert not evidence.exists(), (
        "Browser step was already attempted; inspect and clean this run first"
    )
    write(evidence, {"state": "running", "run": proof["run"]})
    try:
        result = browser_command("open", URL + "/login", "--browser=chrome", "--headed")
        if result.returncode or "### Error" in result.stdout:
            raise RuntimeError("Owned HR browser could not open the verification origin")
        source = (ROOT / "scripts/browser_hr_workflow.js").read_text().strip().removesuffix(";")
        fixture = {key: proof[key] for key in ("run", "accounts", "keys", "dates", "leave_type")}
        with os.fdopen(os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            out.write("async page => (" + source + ")(page," + json.dumps(fixture) + ")")
        result = browser_command("run-code", "--filename=" + str(payload))
        text = result.stdout + result.stderr
        for account in proof["accounts"].values():
            text = text.replace(account["password"], "[redacted]")
        if result.returncode or "### Error" in text:
            reason = text.split("### Error", 1)[-1].split("### Ran Playwright code", 1)[0]
            raise RuntimeError(reason[:1600])
        value = json.loads(text.split("### Result\n", 1)[1].split("\n###", 1)[0])
        value.update(state="complete", run=proof["run"], verified_at=datetime.now(UTC).isoformat())
        write(evidence, value)
        print(json.dumps({"passed": value["passed"], "checks": value["checks"]}))
        assert value["passed"], "HR browser acceptance failed; evidence retained for cleanup"
    except BaseException as error:
        if json.loads(evidence.read_text()).get("state") == "running":
            write(evidence, {"state": "failed", "run": proof["run"], "error": str(error)[:1600]})
        raise
    finally:
        payload.unlink(missing_ok=True)
        close_browser()


class Experiment:
    def __init__(self, proof, client):
        self.proof, self.client = proof, client

    def save(self):
        write(CHECKPOINT, self.proof)

    async def logout(self):
        session = self.proof.get("owned_admin")
        if not session:
            return
        self.client.cookies.clear()
        for cookie in session["cookies"]:
            self.client.cookies.set(**cookie)
        assert (await self.client.post("/api/auth/logout")).status_code == 200
        if "Authorization" in self.client.headers:
            assert (await self.client.get("/api/auth/me")).status_code == 401
        self.proof.setdefault("revoked_admins", []).append(session["id"])
        self.proof.pop("owned_admin")
        self.client.headers.pop("Authorization", None)
        self.client.cookies.clear()
        self.save()

    async def login(self):
        await self.logout()
        credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
        response = await self.client.post("/api/auth/login", json=credentials)
        assert response.status_code == 200
        token = response.json()["access_token"]
        encoded = token.split(".")[1]
        session = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["sid"]
        self.proof["owned_admin"] = {
            "id": str(uuid.UUID(session)),
            "cookies": [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.client.cookies.jar
            ],
        }
        self.client.headers["Authorization"] = "Bearer " + token
        self.save()

    async def read(self, path):
        response = await self.client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        return response.json()

    async def guard(self):
        assert (await self.read("/api/org"))["id"] == ORG
        jobs = await self.read("/api/jobs/status")
        assert jobs["email_mode"] == "test" and not jobs["maintenance"]["active"]

    async def command(self, name, path, body, *, method="POST", expected=201):
        record = self.proof["commands"].setdefault(
            name,
            {"path": path, "body": body, "key": "hr-browser:" + self.proof["run"] + ":" + name},
        )
        assert record["path"] == path and record["body"] == body
        self.save()
        if "result" not in record:
            response = await self.client.request(
                method, path, json=body, headers={"Idempotency-Key": record["key"]}
            )
            assert response.status_code == expected, (name, response.status_code)
            record["result"] = response.json()
            self.save()
        return record["result"]

    async def prepare(self):
        proof = self.proof
        assert not proof.get("cleaned")
        if "baseline" not in proof:
            proof["baseline"] = {
                path: await self.read(path)
                for path in ("/api/accounting/trial-balance", "/api/reports/dashboard")
            }
            self.save()
        for label, permissions in [
            ("employee", ["hr.leave.request"]),
            ("approver", ["hr.leave.request", "hr.leave.approve", "hr.employee.read"]),
        ]:
            account = proof.setdefault("accounts", {}).setdefault(
                label,
                {
                    "email": f"hr-browser-{label}-{proof['run']}@example.com",
                    "password": secrets.token_urlsafe(24),
                    "full_name": f"HR browser {label} {proof['run']}",
                },
            )
            role = await self.command(
                "role-" + label,
                "/api/roles",
                {
                    "code": f"hr-browser-{label}-{proof['run']}",
                    "name": account["full_name"],
                    "permission_codes": permissions,
                },
            )
            user = await self.command(
                "user-" + label,
                "/api/users",
                {
                    **{key: account[key] for key in ("email", "password", "full_name")},
                    "role_codes": [role["code"]],
                },
            )
            employee = await self.command(
                "staff-" + label,
                "/api/hr/employees",
                {"full_name": account["full_name"], "user_id": user["id"]},
            )
            account.update(user_id=user["id"], employee_id=employee["id"])
            self.save()
        proof["leave_type"] = await self.command(
            "type",
            "/api/hr/leave-types",
            {"name": "HR browser leave " + proof["run"], "days_per_year": "5"},
        )
        proof["dates"] = {
            "A": "2031-03-03",
            "B": "2031-03-04",
            "C": "2031-03-05",
            "D": "2031-03-06",
            "self": "2031-03-10",
        }
        await self.command(
            "self-request",
            "/api/hr/leave-requests",
            {
                "employee_id": proof["accounts"]["approver"]["employee_id"],
                "type_id": proof["leave_type"]["id"],
                "date_from": proof["dates"]["self"],
                "date_to": proof["dates"]["self"],
            },
        )
        proof["prepared"] = True
        self.save()

    async def cleanup(self):
        proof = self.proof
        # The browser is closed before this method. Deactivation fences even an
        # unacknowledged in-flight command through its SQL actor/session guard.
        for label, account in proof.get("accounts", {}).items():
            if account.get("user_id"):
                await self.command(
                    "disable-" + label,
                    "/api/users/" + account["user_id"],
                    {"is_active": False},
                    method="PATCH",
                    expected=200,
                )
        # Re-read current state even when an earlier run saved the disable receipt.
        # Do not compensate while a fixture actor could still submit new work.
        owned = {a["user_id"] for a in proof.get("accounts", {}).values() if a.get("user_id")}
        pending, offset = set(owned), 0
        while pending:
            page = await self.read(f"/api/users?limit=100&offset={offset}")
            for user in page["items"]:
                if user["id"] in pending:
                    assert user["is_active"] is False, "Current HR fixture user is not fenced"
                    pending.remove(user["id"])
            offset += len(page["items"])
            assert not pending or (page["items"] and offset < page["total"]), (
                "HR fixture user missing"
            )
        employees = {
            a["employee_id"] for a in proof.get("accounts", {}).values() if a.get("employee_id")
        }
        rows = [
            row
            for row in await self.read("/api/hr/leave-requests")
            if row["employee_id"] in employees
        ]
        for row in rows:
            assert row["type_id"] == proof["leave_type"]["id"]
            if row["status"] == "pending":
                await self.command(
                    "cancel-" + row["id"],
                    "/api/hr/leave-requests/" + row["id"] + "/cancel",
                    None,
                    expected=200,
                )
        rows = [
            row
            for row in await self.read("/api/hr/leave-requests")
            if row["employee_id"] in employees
        ]
        assert all(row["status"] in {"approved", "rejected", "cancelled"} for row in rows)
        for path, baseline in proof["baseline"].items():
            assert await self.read(path) == baseline, "HR probe changed financial/dashboard data"
        proof.update(
            cleaned=True, cleaned_at=datetime.now(UTC).isoformat(), retained_requests=len(rows)
        )
        for account in proof.get("accounts", {}).values():
            account.pop("password", None)
        for name, record in proof["commands"].items():
            if name.startswith("user-"):
                record["body"].pop("password", None)
        self.save()
        print(
            "PASS HR fixtures fenced, pending requests cancelled and financial baseline preserved"
        )


async def main(step, fresh=False):
    assert not fresh or step == "prepare", "A fresh run must start with prepare"
    if fresh and CHECKPOINT.exists():
        prior = json.loads(CHECKPOINT.read_text())
        assert step == "prepare" and prior.get("cleaned") and not prior.get("owned_admin")
        CHECKPOINT.rename(DIRECTORY / ("workflow-" + prior["run"] + ".json"))
        if (DIRECTORY / "browser.json").exists():
            (DIRECTORY / "browser.json").rename(DIRECTORY / ("browser-" + prior["run"] + ".json"))
    proof = (
        json.loads(CHECKPOINT.read_text())
        if CHECKPOINT.exists()
        else {
            "run": uuid.uuid4().hex[:12],
            "url": URL,
            "commands": {},
            "keys": {name: str(uuid.uuid4()) for name in KEYS},
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    assert proof["url"] == URL
    if step == "browser":
        run_browser(proof)
        return
    if step == "cleanup":
        close_browser()
    async with httpx.AsyncClient(
        base_url=URL,
        headers={"Origin": URL},
        timeout=180,
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2),
    ) as client:
        experiment = Experiment(proof, client)
        await experiment.login()
        try:
            await experiment.guard()
            if step == "prepare":
                await experiment.prepare()
                print("PASS isolated HR browser fixtures prepared; email stays in test mode")
            else:
                await experiment.cleanup()
        finally:
            await experiment.logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for action in ("prepare", "browser", "cleanup"):
        group.add_argument("--" + action, action="store_true")
    parser.add_argument("--new", action="store_true")
    args = parser.parse_args()
    with owned_run():
        asyncio.run(
            main("prepare" if args.prepare else "browser" if args.browser else "cleanup", args.new)
        )
