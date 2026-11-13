"""HR ownership, concurrent allowances, exact dates and atomic notifications."""

import sqlite3
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.shared.working_days import working_days_by_year

from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.hr_commands import Leave
from erp_cf.identity import from_token
from erp_cf.store import UnitOfWork
from tests.test_auth import login


async def setup(client, env, days="5"):
    token = await login(client)
    headers = {"Authorization": "Bearer " + token}
    actor = await from_token(env, token)

    async def create(path, data):
        response = await client.post("/api/hr/" + path, headers=headers, json=data)
        assert response.status_code == 201, response.text
        return response.json()

    employee = await create("employees", {"full_name": "Test employee"})
    leave_type = await create(
        "leave-types", {"name": "Annual", "days_per_year": days, "accrues": True}
    )
    assert leave_type["accrues"] is True
    return headers, actor, employee, leave_type


def body(employee, leave_type, first="2030-03-04", last="2030-03-06"):
    return {
        "employee_id": employee["id"],
        "type_id": leave_type["id"],
        "date_from": first,
        "date_to": last,
    }


def plan(env, actor):
    return UnitOfWork(D1Database(env.DB), env.ORG_ID, actor.id, str(uuid.uuid4()), "hr-test", {})


def snapshot(env):
    return {
        name: [
            tuple(row) for row in env.DB.connection.execute(f"SELECT * FROM {name} ORDER BY rowid")
        ]
        for name in [
            "hr_employees",
            "hr_leave_requests",
            "core_notifications",
            "core_audit_logs",
            "cf_commands",
            "cf_outbox",
        ]
    }


def test_working_days_matches_daily_oracle_across_leap_years_and_date_boundaries():
    for start in [date(2024, 2, 27), date(2026, 12, 28), date(2027, 1, 1), date(9999, 12, 25)]:
        for length in range(min(100, (date.max - start).days + 1)):
            end, expected = start + timedelta(days=length), {}
            for offset in range(length + 1):
                current = start + timedelta(days=offset)
                if current.weekday() < 5:
                    expected[current.year] = expected.get(current.year, Decimal(0)) + 1
            assert working_days_by_year(start, end) == expected
    assert working_days_by_year(date.max, date.min) == {}


async def test_nonoverlapping_competing_plans_cannot_exceed_annual_allowance(client, env):
    headers, actor, employee, leave_type = await setup(client, env)
    plans = [plan(env, actor) for _ in range(2)]
    requests = []
    for write, (first, last) in zip(
        plans, [("2030-03-04", "2030-03-06"), ("2030-04-01", "2030-04-03")], strict=True
    ):
        requests.append(
            await Leave(write, actor).create({"body": body(employee, leave_type, first, last)})
        )
    await plans[0].commit({"created": True})
    after = snapshot(env)
    with pytest.raises(ConflictError):
        await plans[1].commit({"created": True})
    assert snapshot(env) == after
    response = await client.post(
        "/api/hr/leave-requests",
        headers=headers,
        json=body(employee, leave_type, "2030-04-01", "2030-04-03"),
    )
    assert response.status_code == 422 and "annual allowance" in response.text
    assert env.DB.connection.execute("SELECT COUNT(*) FROM hr_leave_requests").fetchone()[0] == 1


async def test_request_faults_leave_balances_notifications_and_receipts_unchanged(client, env):
    headers, _, employee, leave_type = await setup(client, env)
    before = snapshot(env)
    for target in [
        'UPDATE "hr_employees"',
        'INSERT INTO "hr_leave_requests"',
        'INSERT INTO "core_audit_logs"',
        'INSERT INTO "core_notifications"',
    ]:

        async def fail(statements, target=target):
            index = next(i for i, stmt in enumerate(statements) if stmt.sql.startswith(target))
            statements.insert(
                index + 1,
                env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('failure',0)"),
            )

        env.DB.before_batch = fail
        response = await client.post(
            "/api/hr/leave-requests",
            headers={**headers, "Idempotency-Key": "leave-retry"},
            json=body(employee, leave_type),
        )
        assert response.status_code == 409, response.text
        env.DB.before_batch = None
        assert snapshot(env) == before
    response = await client.post(
        "/api/hr/leave-requests",
        headers={**headers, "Idempotency-Key": "leave-retry"},
        json=body(employee, leave_type),
    )
    assert response.status_code == 201, response.text
    request = response.json()
    notes = (await client.get("/api/notifications", headers=headers)).json()["items"]
    assert sum(row["payload"].get("request_id") == request["id"] for row in notes) == 1


async def test_competing_decisions_commit_one_audit_and_protect_finalized_history(client, env):
    headers, actor, employee, leave_type = await setup(client, env)
    response = await client.post(
        "/api/hr/leave-requests", headers=headers, json=body(employee, leave_type)
    )
    assert response.status_code == 201, response.text
    row = response.json()
    plans = [plan(env, actor) for _ in range(2)]
    for write, decision in zip(plans, ["approved", "rejected"], strict=True):
        await Leave(write, actor).decide({"id": row["id"]}, decision)
    await plans[0].commit({"approved": True})
    after = snapshot(env)
    with pytest.raises(ConflictError):
        await plans[1].commit({"rejected": True})
    assert snapshot(env) == after
    response = await client.post(f"/api/hr/leave-requests/{row['id']}/cancel", headers=headers)
    assert response.status_code == 409
    for sql in [
        "UPDATE hr_leave_requests SET status='pending'",
        "UPDATE hr_leave_requests SET days=days",
        "DELETE FROM hr_leave_requests",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            env.DB.connection.execute(sql)


async def test_annual_allowance_splits_years_and_rejects_weekend_only_requests(client, env):
    headers, _, employee, leave_type = await setup(client, env, "1")
    response = await client.post(
        "/api/hr/leave-requests",
        headers=headers,
        json=body(employee, leave_type, "2030-12-31", "2031-01-01"),
    )
    assert response.status_code == 201 and response.json()["days"] == "2.0"
    for first, last in [
        ("2030-12-30", "2030-12-30"),
        ("2031-01-02", "2031-01-02"),
        ("2031-01-04", "2031-01-05"),
    ]:
        before = snapshot(env)
        response = await client.post(
            "/api/hr/leave-requests", headers=headers, json=body(employee, leave_type, first, last)
        )
        assert response.status_code == 422, response.text
        assert snapshot(env) == before


async def test_self_decision_and_foreign_department_are_rejected_without_side_effects(client, env):
    headers, actor, _, leave_type = await setup(client, env)
    response = await client.post(
        "/api/hr/employees",
        headers=headers,
        json={"full_name": "Self approver", "user_id": actor.id},
    )
    assert response.status_code == 201, response.text
    employee = response.json()
    response = await client.post(
        "/api/hr/leave-requests", headers=headers, json=body(employee, leave_type)
    )
    assert response.status_code == 201, response.text
    row = response.json()
    for action in ["approve", "reject"]:
        before = snapshot(env)
        response = await client.post(
            f"/api/hr/leave-requests/{row['id']}/{action}", headers=headers
        )
        assert response.status_code == 403, response.text
        assert snapshot(env) == before
    foreign = plan(env, actor)
    foreign.org_id = str(uuid.uuid7())
    department = foreign.add("hr_departments", {"name": "Other org"})
    await foreign.commit({})
    before = snapshot(env)
    response = await client.post(
        "/api/hr/employees",
        headers=headers,
        json={"full_name": "Wrong department", "department_id": department["id"]},
    )
    assert response.status_code == 404, response.text
    assert snapshot(env) == before


async def test_remote_hr_acceptance_verifier_against_native_binding(env, tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import httpx

    from erp_cf.app import create_app
    from scripts import verify_hr_remote

    credentials = tmp_path / ".demo.app-staging.json"
    credentials.write_text(json.dumps({"email": "admin@example.com", "password": "admin123"}))
    credentials.chmod(0o600)
    monkeypatch.setattr(verify_hr_remote, "ROOT", tmp_path)
    native_client = httpx.AsyncClient
    application = create_app(env)

    def client(**kwargs):
        return native_client(**kwargs, transport=httpx.ASGITransport(app=application))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    env.DB.connection.executescript(
        (Path(__file__).parents[1] / "fixtures/hr_fault_probe.sql").read_text()
    )
    await verify_hr_remote.main(failure_probe=True)
