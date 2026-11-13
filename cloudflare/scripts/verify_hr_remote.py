"""Isolated staging HR acceptance; no emails or credentials are logged/sent.

Failure injection requires fixtures/hr_fault_probe.sql; remove it afterwards.
"""

import argparse
import asyncio
import json
import secrets
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"
PROBE = "Cloudflare HR atomic rollback probe"


async def main(failure_probe=False):
    run = uuid.uuid4().hex[:10]
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    async with httpx.AsyncClient(base_url=URL, timeout=180) as client:

        async def login(email, password):
            response = await client.post(
                "/api/auth/login", json={"email": email, "password": password}
            )
            assert response.status_code == 200, f"Login returned {response.status_code}"
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        admin = await login(credentials["email"], credentials["password"])

        async def send(method, path, body=None, *, auth=None, key=None):
            headers = dict(auth or admin)
            if method != "GET":
                headers["Idempotency-Key"] = key or uuid.uuid4().hex
            for attempt in range(3):
                try:
                    return await client.request(method, path, json=body, headers=headers)
                except httpx.TransportError:
                    if attempt == 2:
                        raise

        async def call(method, path, body=None, *, expected=200, auth=None, key=None):
            response = await send(method, path, body, auth=auth, key=key)
            assert response.status_code == expected, (
                f"{method} {path}: {response.status_code}: {response.text[:400]}"
            )
            return response.json() if response.content else None

        async def create(path, body):
            return await call("POST", path, body, expected=201)

        department = await create("/api/hr/departments", {"name": f"HR proof {run}"})
        employee = await create(
            "/api/hr/employees", {"full_name": f"HR proof {run}", "department_id": department["id"]}
        )
        leave_type = await create(
            "/api/hr/leave-types", {"name": f"Annual {run}", "days_per_year": "5", "accrues": True}
        )
        assert leave_type["accrues"] is True

        def request(employee, first="2030-03-04", last="2030-03-06", **fields):
            return {
                "employee_id": employee["id"],
                "type_id": leave_type["id"],
                "date_from": first,
                "date_to": last,
                **fields,
            }

        if failure_probe:
            body = request(employee, reason=PROBE)
            key = f"hr-fault-{run}"
            await call("POST", "/api/hr/leave-requests", body, expected=409, key=key)
            rows = await call("GET", "/api/hr/leave-requests")
            assert not any(row["employee_id"] == employee["id"] for row in rows)
            body["reason"] = "Valid retry after rollback"
            row = await call("POST", "/api/hr/leave-requests", body, expected=201, key=key)
            replay = await call("POST", "/api/hr/leave-requests", body, expected=201, key=key)
            assert row == replay
            notes = (await call("GET", "/api/notifications?limit=100"))["items"]
            assert sum(n["payload"].get("request_id") == row["id"] for n in notes) == 1
            await call("POST", f"/api/hr/leave-requests/{row['id']}/cancel")
            print(
                "PASS: real D1 notification failure rolls back leave, audit and command receipt",
                flush=True,
            )

        rows = await asyncio.gather(
            *[
                call(
                    "POST",
                    "/api/hr/leave-requests",
                    request(employee),
                    expected=201,
                    key=f"hr-once-{run}",
                )
                for _ in range(5)
            ]
        )
        assert all(row == rows[0] for row in rows)
        first = rows[0]
        await call(
            "POST",
            "/api/hr/leave-requests",
            request(employee, "2030-03-05", "2030-03-07"),
            expected=409,
        )
        await call(
            "POST",
            "/api/hr/leave-requests",
            request(employee, "2030-04-01", "2030-04-03"),
            expected=422,
        )
        await call(
            "POST",
            "/api/hr/leave-requests",
            request(employee, "2030-03-09", "2030-03-10"),
            expected=422,
        )
        decisions = await asyncio.gather(
            *[
                send("POST", f"/api/hr/leave-requests/{first['id']}/{action}")
                for action in ["approve", "reject"]
            ]
        )
        assert sorted(row.status_code for row in decisions) == [200, 409]
        assert next(row.json() for row in decisions if row.status_code == 200)["status"] in {
            "approved",
            "rejected",
        }
        await call("POST", f"/api/hr/leave-requests/{first['id']}/cancel", expected=409)
        print(
            "PASS: retries, pending allowance, overlap/weekend guards "
            "and competing final decisions",
            flush=True,
        )

        async def user(label, permissions):
            role = await create(
                "/api/roles",
                {"code": f"{label}-{run}", "name": label, "permission_codes": permissions},
            )
            email, password = f"{label}-{run}@example.com", secrets.token_urlsafe(24)
            user = await create(
                "/api/users",
                {
                    "email": email,
                    "password": password,
                    "full_name": label,
                    "role_codes": [role["code"]],
                },
            )
            employee = await create(
                "/api/hr/employees", {"full_name": label, "user_id": user["id"]}
            )
            return user, employee, await login(email, password)

        self_user, self_employee, self_auth = await user("leave-self", ["hr.leave.request"])
        hr_user, hr_employee, hr_auth = await user(
            "leave-approver", ["hr.leave.request", "hr.leave.approve"]
        )
        assert (await call("GET", "/api/hr/employees/me", auth=self_auth))["id"] == self_employee[
            "id"
        ]
        await call("GET", "/api/hr/employees", auth=self_auth, expected=403)
        await call(
            "POST", "/api/hr/leave-requests", request(employee), auth=self_auth, expected=403
        )
        own = await call(
            "POST", "/api/hr/leave-requests", request(self_employee), auth=self_auth, expected=201
        )
        visible = await call("GET", "/api/hr/leave-requests", auth=self_auth)
        assert [row["id"] for row in visible] == [own["id"]]
        await call(
            "POST", f"/api/hr/leave-requests/{own['id']}/approve", auth=self_auth, expected=403
        )
        approved = await call("POST", f"/api/hr/leave-requests/{own['id']}/approve", auth=hr_auth)
        assert approved["status"] == "approved"
        notes = (await call("GET", "/api/notifications?limit=100", auth=self_auth))["items"]
        assert (
            sum(
                n["type"] == "leave_approved" and n["payload"].get("request_id") == own["id"]
                for n in notes
            )
            == 1
        )
        hr_own = await call(
            "POST", "/api/hr/leave-requests", request(hr_employee), auth=hr_auth, expected=201
        )
        for action in ["approve", "reject"]:
            await call(
                "POST",
                f"/api/hr/leave-requests/{hr_own['id']}/{action}",
                auth=hr_auth,
                expected=403,
            )
        await call(
            "POST", f"/api/hr/leave-requests/{hr_own['id']}/cancel", auth=self_auth, expected=403
        )
        cancelled = await call(
            "POST", f"/api/hr/leave-requests/{hr_own['id']}/cancel", auth=hr_auth
        )
        assert cancelled["status"] == "cancelled"
        print(
            "PASS: self-service ownership, scoped reads, no self-approval "
            "and atomic decision notification",
            flush=True,
        )

        year_type = await create(
            "/api/hr/leave-types", {"name": f"Year boundary {run}", "days_per_year": "1"}
        )
        body = request(employee, "2030-12-31", "2031-01-01", type_id=year_type["id"])
        row = await call("POST", "/api/hr/leave-requests", body, expected=201)
        assert row["days"] == "2.0"
        await call(
            "POST",
            "/api/hr/leave-requests",
            request(employee, "2031-01-02", "2031-01-02", type_id=year_type["id"]),
            expected=422,
        )
        await call("POST", f"/api/hr/leave-requests/{row['id']}/cancel")
        for user_row in [self_user, hr_user]:
            await call("PATCH", f"/api/users/{user_row['id']}", {"is_active": False})
        print(
            "PASS: cross-year allowances and cancellation; synthetic user sessions revoked",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--failure-probe", action="store_true")
    asyncio.run(main(parser.parse_args().failure_probe))
