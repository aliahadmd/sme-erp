"""A paginated backup must pause all writers across requests and restarts."""

import asyncio
import sqlite3
import uuid

import pytest

from erp_cf.background import dispatch
from erp_cf.db import D1Database, Statement
from erp_cf.errors import ConflictError, DomainError
from erp_cf.maintenance import LeaseLost, acquire, coverage, protected, release, renew, state
from erp_cf.store import UnitOfWork
from tests.jobs_fakes import deliver, enable
from tests.test_auth import login
from tests.test_background import message, rows
from tests.test_inventory import setup
from tests.test_invoicing import invoice
from tests.test_mail import pending


async def test_write_pause_preserves_reads_and_blocks_commands_auth_and_transport(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    doc = await invoice(client, headers, party)
    # Sign-in creates a refresh family before taking the lease.
    await login(client)
    db = D1Database(env.DB)
    before = {
        table: rows(env, "SELECT * FROM " + table)
        for table in [
            "core_users",
            "platform_sessions",
            "platform_refresh_tokens",
            "crm_contacts",
            "cf_commands",
            "cf_outbox",
            "core_audit_logs",
            "cf_business_epochs",
        ]
    }
    lease = await acquire(db, uuid.uuid4().hex)
    assert (await state(db))["active"] == 1
    assert (
        await client.get(f"/api/invoicing/invoices/{doc['id']}", headers=headers)
    ).status_code == 200
    attempts = [
        await client.patch(
            "/api/crm/contacts/" + party["id"], headers=headers, json={"name": "Blocked change"}
        ),
        await client.post(
            "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
        ),
        await client.post("/api/auth/refresh"),
        await client.post("/api/auth/logout"),
    ]
    for response in attempts:
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "maintenance"
    with pytest.raises(DomainError, match="writes are paused"):
        await dispatch(env)
    # Dispatch may have sent duplicate IDs before its blocked metadata write.
    # Source records remain pending and their business effects cannot run.
    event = rows(env, "SELECT id FROM cf_outbox WHERE event_type='invoice.posted'")[0]["id"]
    queued = message(env, event)
    await deliver(env, queued)
    assert queued.retry_options and not queued.acknowledged
    assert {table: rows(env, "SELECT * FROM " + table) for table in before} == before
    assert not rows(env, "SELECT * FROM cf_assertions")
    await release(db, lease)
    assert not (await state(db))["active"]
    response = await client.patch(
        "/api/crm/contacts/" + party["id"], headers=headers, json={"name": "Allowed after backup"}
    )
    assert response.status_code == 200, response.text


async def test_trigger_blocks_writers_that_do_not_use_python_adapter(env):
    lease = await acquire(D1Database(env.DB), "database-guard-proof")
    for sql in [
        "UPDATE core_users SET full_name='Forbidden'",
        "DELETE FROM core_user_roles",
        "INSERT INTO core_permissions(id,code,module,action) VALUES ('x','x','x','x')",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="erp_maintenance"):
            env.DB.connection.execute(sql)
    await release(D1Database(env.DB), lease)


async def test_active_mail_claim_must_drain_before_global_write_pause(client, env):
    from tests.test_mail import claim, finish

    _, _, _, mail = await pending(client, env)
    payload = await claim(env, mail)
    with pytest.raises(ConflictError, match="mail send"):
        await acquire(D1Database(env.DB), "waiting-backup")
    assert not (await state(D1Database(env.DB)))["active"]
    await finish(env, payload)
    lease = await acquire(D1Database(env.DB), "ready-backup")
    await release(D1Database(env.DB), lease)


async def test_no_email_send_can_start_after_pause(client, env):
    _, _, _, mail = await pending(client, env)
    lease = await acquire(D1Database(env.DB), "mail-pause")
    item = message(env, mail["delivery_event_id"])
    await deliver(env, item)
    assert item.retry_options and not env.EMAIL.calls
    assert not rows(env, "SELECT * FROM cf_mail_attempts")
    await release(D1Database(env.DB), lease)


async def test_lease_owner_fence_expiry_and_restart_are_authoritative(env):
    db = D1Database(env.DB)
    first = await acquire(db, "first")
    same = await acquire(D1Database(env.DB), "first")
    assert same == first
    with pytest.raises(ConflictError):
        await acquire(db, "second")
    await renew(D1Database(env.DB), first, seconds=120)
    result = await protected(D1Database(env.DB), first, [Statement("SELECT 'exact' AS value")])
    assert result[0]["results"] == [{"value": "exact"}]
    # Simulate a crashed controller: DB time expires the pause with no alarm or
    # live object required. A resumed stale snapshot must fail its next guard.
    env.DB.connection.execute("UPDATE platform_maintenance SET expires_at=0")
    assert not (await state(db))["active"]
    env.DB.connection.execute("UPDATE core_users SET full_name='After lease expiry'")
    for action in [renew, release]:
        with pytest.raises(LeaseLost):
            await action(db, first)
    second = await acquire(db, "second")
    assert int(second.fence) == int(first.fence) + 1
    with pytest.raises(LeaseLost):
        await release(db, first)
    assert (await state(db))["active"]
    await release(db, second)


async def test_competing_acquisitions_have_one_winner(env):
    results = await asyncio.gather(
        acquire(D1Database(env.DB), "one"),
        acquire(D1Database(env.DB), "two"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert not rows(env, "SELECT * FROM cf_assertions")


async def test_new_or_modified_schema_cannot_be_backed_up_without_all_guards(env):
    db = D1Database(env.DB)
    assert len(await coverage(db)) == 64
    env.DB.connection.execute("CREATE TABLE unguarded(id TEXT PRIMARY KEY) STRICT")
    with pytest.raises(DomainError, match="missing a verified"):
        await acquire(db, "unsafe-backup")
    assert not (await state(db))["active"]
    env.DB.connection.execute("DROP TABLE unguarded")
    env.DB.connection.execute("DROP TRIGGER cf_quiesce_cf_outbox_update")
    with pytest.raises(DomainError, match="missing a verified"):
        await acquire(db, "unsafe-backup")


async def test_command_planned_before_pause_cannot_commit_inside_snapshot_window(client, env):
    headers, _, _, party = await setup(client)
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "stale-plan", {})
    row = await plan.repository("crm_contacts").get(party["id"])
    plan.update("crm_contacts", row, {"name": "Planned before pause"})
    lease = await acquire(D1Database(env.DB), "snapshot")
    with pytest.raises(DomainError, match="writes are paused"):
        await plan.commit({})
    assert not rows(env, "SELECT * FROM cf_commands WHERE id=?", (plan.identity,))
    assert (await client.get("/api/crm/contacts/" + party["id"], headers=headers)).json()[
        "name"
    ] == party["name"]
    await release(D1Database(env.DB), lease)
    await plan.commit({})


async def test_lease_claim_rolls_back_when_database_response_is_lost_before_commit(env):
    db, native = D1Database(env.DB), env.DB.batch

    async def fail(statements):
        env.DB.connection.execute("BEGIN")
        try:
            for statement in statements:
                await statement.all()
            raise OSError("Interrupted lease transaction")
        finally:
            env.DB.connection.rollback()

    env.DB.batch = fail
    with pytest.raises(OSError):
        await acquire(db, "interrupted")
    env.DB.batch = native
    assert not (await state(db))["active"] and not rows(env, "SELECT * FROM cf_assertions")


async def test_lost_committed_claim_response_is_recovered_with_the_same_owner(env):
    db, native = D1Database(env.DB), env.DB.batch

    async def lost_response(statements):
        await native(statements)
        raise OSError("Committed lease response was lost")

    env.DB.batch = lost_response
    with pytest.raises(OSError):
        await acquire(db, "durable-owner")
    env.DB.batch = native
    assert (await state(db))["active"]
    resumed = await acquire(D1Database(env.DB), "durable-owner")
    assert resumed.fence == "1"
    await release(D1Database(env.DB), resumed)
