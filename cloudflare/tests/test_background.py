"""Outbox delivery, stale replay, atomic inbox effects and resumable maintenance."""

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from erp_cf.background import (
    dispatch,
    quarantine,
    record_failure,
    recover_dead_letters,
    run_maintenance,
    system,
    tick,
)
from erp_cf.db import D1Database
from erp_cf.job_commands import consume_event
from erp_cf.notifications import Notifications
from erp_cf.store import UnitOfWork
from tests.jobs_fakes import Message, deliver, drain, enable
from tests.test_auth import login
from tests.test_inventory import create, post, receipt, setup
from tests.test_invoicing import invoice, payment


def rows(env, sql, params=()):
    return [dict(row) for row in env.DB.connection.execute(sql, params)]


async def event(env, kind, payload=None):
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "test.event", {})
    identity = plan.emit(kind, payload or {})
    await plan.commit({})
    return identity


def message(env, identity, *, generation=0, attempts=1):
    return Message(
        json.dumps(
            {"version": 1, "org_id": env.ORG_ID, "event_id": identity, "generation": generation}
        ),
        attempts=attempts,
    )


async def test_invoice_payment_notifications_are_eventual_and_duplicate_safe(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    updated = await client.patch(
        "/api/crm/contacts/" + party["id"],
        headers=headers,
        json={"emails": [{"value": "customer@example.com"}]},
    )
    assert updated.status_code == 200, updated.text
    row = await invoice(client, headers, party, due_date="2020-01-01")
    pay = await payment(client, headers, party, "10", target=row)
    assert not rows(env, "SELECT * FROM cf_event_receipts")
    messages = await drain(env)
    expected = {"invoice_posted", "invoice_overdue", "payment_recorded"}
    assert {r["type"] for r in rows(env, "SELECT type FROM core_notifications")} == expected
    mail = rows(env, "SELECT status,kind,body FROM cf_mail_outbox")
    assert len(mail) == 4 and {r["status"] for r in mail} == {"disabled"}
    assert next(r for r in mail if r["kind"] == "customer")["body"].find(row["number"]) >= 0
    receipts = rows(env, "SELECT * FROM cf_event_receipts")
    await deliver(env, *[Message(m.body) for m in messages])
    assert rows(env, "SELECT * FROM cf_event_receipts") == receipts
    assert len(rows(env, "SELECT * FROM core_notifications")) == 3
    assert len(rows(env, "SELECT * FROM cf_mail_outbox")) == 4
    assert pay["status"] == "recorded"
    await run_maintenance(env, "overdue", "daily", date.today())
    await run_maintenance(env, "overdue", "tomorrow", date.today() + timedelta(days=1))
    # Event-time notice must not consume the independent customer reminder.
    assert len(rows(env, "SELECT * FROM cf_mail_outbox WHERE kind='overdue'")) == 1
    assert len(rows(env, "SELECT * FROM core_notifications WHERE type='invoice_overdue'")) == 1


async def test_opt_out_does_not_remove_in_app_notice_and_inactive_users_are_excluded(client, env):
    enable(env, mode="test")
    headers, _, _, party = await setup(client)
    env.DB.connection.execute(
        "UPDATE core_users SET email_prefs=?", (json.dumps({"invoice_sent": False}),)
    )
    await invoice(client, headers, party)
    await drain(env)
    assert len(rows(env, "SELECT * FROM core_notifications")) == 1
    assert not rows(env, "SELECT * FROM cf_mail_outbox")
    await invoice(client, headers, party)
    env.DB.connection.execute("UPDATE core_users SET is_active=0")
    await drain(env)
    assert len(rows(env, "SELECT * FROM core_notifications")) == 1


async def test_notification_permissions_and_operational_routes_remain_restricted(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    role = await client.post(
        "/api/roles",
        headers=headers,
        json={
            "code": "invoice_reader",
            "name": "Invoice reader",
            "permission_codes": ["invoicing.invoice.read"],
        },
    )
    assert role.status_code == 201, role.text
    users = []
    for name, codes in [("reader", ["invoice_reader"]), ("unprivileged", [])]:
        created = await client.post(
            "/api/users",
            headers=headers,
            json={
                "email": f"{name}@example.com",
                "full_name": name,
                "password": "test-password-123",
                "role_codes": codes,
            },
        )
        assert created.status_code == 201, created.text
        users.append(created.json())
    await invoice(client, headers, party)
    await drain(env)
    notes = rows(env, "SELECT id,user_id FROM core_notifications")
    assert len(notes) == 2
    assert users[0]["id"] in {row["user_id"] for row in notes}
    assert users[1]["id"] not in {row["user_id"] for row in notes}
    login_response = await client.post(
        "/api/auth/login",
        json={
            "email": users[0]["email"],
            "password": "test-password-123",
        },
    )
    reader = {"Authorization": "Bearer " + login_response.json()["access_token"]}
    for path in ["/api/jobs/status", "/api/jobs/dead-letters"]:
        assert (await client.get(path, headers=reader)).status_code == 403
    assert (
        await client.post("/api/jobs/dead-letters/" + "0" * 64 + "/replay", headers=reader)
    ).status_code == 403
    assert (
        await client.post(
            "/internal/jobs/tick",
            json={},
            headers={
                **reader,
                "X-Bootstrap-Token": env.BOOTSTRAP_TOKEN,
            },
        )
    ).status_code == 403
    private_note = next(row["id"] for row in notes if row["user_id"] != users[0]["id"])
    assert (
        await client.post(f"/api/notifications/{private_note}/read", headers=reader)
    ).status_code == 404


async def test_low_stock_notifications_and_late_void_suppression(client, env):
    enable(env)
    headers, warehouse, product, party = await setup(client)
    await receipt(client, headers, product, "6", "10")
    delivery = await create(
        client,
        headers,
        "deliveries",
        {
            "warehouse_id": warehouse["id"],
            "party_id": party["id"],
            "lines": [{"product_id": product["id"], "qty": "2", "unit_price": "20"}],
        },
    )
    await post(client, headers, "deliveries", delivery)
    row = await invoice(client, headers, party)
    result = await client.post(f"/api/invoicing/invoices/{row['id']}/void", headers=headers)
    assert result.status_code == 200, result.text
    await drain(env)
    notices = rows(env, "SELECT type,payload FROM core_notifications")
    assert len(notices) == 1 and notices[0]["type"] == "low_stock"
    assert json.loads(notices[0]["payload"])["qty_on_hand"] == "4.0000"
    assert rows(env, "SELECT outcome FROM cf_event_receipts WHERE outcome='stale_source'")


async def test_event_batch_rolls_back_at_every_statement_and_retries_once(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    row = await invoice(client, headers, party, due_date="2020-01-01")
    identity = rows(env, "SELECT id FROM cf_outbox WHERE event_type='invoice.posted'")[0]["id"]
    original = env.DB.batch
    batch_size = None
    failure_at = 0

    async def failing_batch(statements):
        nonlocal batch_size
        batch_size = len(statements)
        env.DB.connection.execute("BEGIN")
        try:
            for index, statement in enumerate(statements):
                await statement.all()
                if index == failure_at:
                    raise RuntimeError("injected ambiguous job failure")
            raise AssertionError("Failure index must be in the batch")
        finally:
            env.DB.connection.rollback()

    env.DB.batch = failing_batch
    envelope = {
        "operation": "jobs.event",
        "key": "inbox-fault-proof",
        "payload": {"event_id": identity, "generation": 0},
    }
    tables = [
        "core_notifications",
        "cf_notification_keys",
        "cf_mail_outbox",
        "cf_event_receipts",
        "cf_commands",
        "cf_outbox",
        "cf_business_epochs",
    ]
    before = {name: rows(env, f"SELECT * FROM {name}") for name in tables}
    try:
        assert (await env.COMMANDS.coordinator.run_system(envelope))["status"] == 500
        assert batch_size > 10
        for failure_at in range(1, batch_size):
            assert failure_at < batch_size
            assert (await env.COMMANDS.coordinator.run_system(envelope))["status"] == 500
            assert {name: rows(env, f"SELECT * FROM {name}") for name in tables} == before
    finally:
        env.DB.batch = original
    first, duplicate = await asyncio.gather(
        env.COMMANDS.coordinator.run_system(envelope), env.COMMANDS.coordinator.run_system(envelope)
    )
    assert first == duplicate and first["status"] == 200
    assert len(rows(env, "SELECT * FROM cf_event_receipts")) == 1
    assert len(rows(env, "SELECT * FROM core_notifications")) == 2
    assert row["status"] == "posted"


async def test_dispatch_recovers_ambiguous_send_and_does_not_reset_completed_event(env):
    queue = enable(env)
    identity = await event(env, "receipt.posted")

    async def lost_response():
        raise OSError("send accepted but response lost")

    queue.after_send = lost_response
    with pytest.raises(OSError):
        await dispatch(env)
    assert rows(env, "SELECT dispatched_at FROM cf_outbox")[0]["dispatched_at"] is None
    queue.after_send = None
    await dispatch(env)
    assert len(queue.messages) == 2
    await deliver(env, *[Message(item["body"]) for item in queue.messages])
    assert len(rows(env, "SELECT * FROM cf_event_receipts")) == 1
    assert (await dispatch(env))["dispatched"] == 0
    second = await event(env, "receipt.posted")

    async def consumed_before_mark():
        await deliver(env, message(env, second))

    queue.after_send = consumed_before_mark
    await dispatch(env)
    assert rows(env, "SELECT delivery_state FROM cf_outbox WHERE id=?", (second,))[0] == {
        "delivery_state": "complete"
    }
    assert identity != second


async def test_dead_letter_repeated_replay_ignores_stale_generations(client, env):
    enable(env)
    token = await login(client)
    headers = {"Authorization": "Bearer " + token}
    identity = await event(env, "unhandled.poison", {"private": "do-not-log"})
    old = message(env, identity, attempts=4)
    await deliver(env, old)
    assert old.retry_options and not old.acknowledged
    assert rows(env, "SELECT delivery_state FROM cf_outbox")[0]["delivery_state"] == "dead"
    await deliver(env, old, dead=True)
    assert old.acknowledged
    letter = rows(env, "SELECT id FROM cf_dead_letters")[0]["id"]
    path = "/api/jobs/dead-letters/" + letter + "/replay"
    assert (await client.post(path)).status_code == 401
    replay = await client.post(path, headers={**headers, "Idempotency-Key": "replay-one"})
    assert replay.status_code == 200, replay.text
    await quarantine(env, old)
    await record_failure(env, identity, 0, "old_failure", final=True)
    assert rows(env, "SELECT delivery_state FROM cf_outbox")[0]["delivery_state"] == "pending"
    fresh = message(env, identity, generation=1, attempts=4)
    await deliver(env, fresh)
    await deliver(env, fresh, dead=True)
    letters = rows(env, "SELECT id,state,delivery_generation FROM cf_dead_letters ORDER BY rowid")
    assert [r["state"] for r in letters] == ["replayed", "quarantined"]
    replay2 = await client.post(
        "/api/jobs/dead-letters/" + letters[1]["id"] + "/replay", headers=headers
    )
    assert replay2.status_code == 200, replay2.text
    # A repaired subscriber/source is processed only once in the new generation.
    env.DB.connection.execute(
        "UPDATE cf_outbox SET event_type='receipt.posted' WHERE id=?", (identity,)
    )
    good = message(env, identity, generation=2)
    await deliver(env, good, message(env, identity, generation=0))
    assert good.acknowledged
    await quarantine(env, fresh)
    assert {r["state"] for r in rows(env, "SELECT state FROM cf_dead_letters")} == {"resolved"}
    assert rows(env, "SELECT delivery_state FROM cf_outbox")[0]["delivery_state"] == "complete"
    assert len(rows(env, "SELECT * FROM cf_event_receipts")) == 1


async def test_invalid_or_foreign_message_is_quarantined_without_payload_disclosure(env, caplog):
    enable(env)
    secret = "sensitive@example.com"
    invalid = Message(secret * 100)
    foreign = Message(
        json.dumps(
            {
                "version": 1,
                "org_id": str(uuid.uuid4()),
                "event_id": str(uuid.uuid4()),
                "generation": 0,
            }
        )
    )
    await deliver(env, invalid, foreign)
    assert not invalid.acknowledged and not foreign.acknowledged
    await deliver(env, invalid, foreign, dead=True)
    assert invalid.acknowledged and foreign.acknowledged
    assert len(rows(env, "SELECT * FROM cf_dead_letters")) == 2
    assert secret not in caplog.text
    assert secret not in json.dumps(rows(env, "SELECT * FROM cf_dead_letters"))


async def test_quarantine_database_failure_retries_instead_of_acknowledging(env):
    enable(env)
    identity = await event(env, "receipt.posted")
    item = message(env, identity)
    original = env.DB.batch

    async def unavailable(statements):
        raise OSError("D1 unavailable")

    env.DB.batch = unavailable
    try:
        await deliver(env, item, dead=True)
        assert not item.acknowledged and item.retry_options
    finally:
        env.DB.batch = original
    await deliver(env, item, dead=True)
    assert item.acknowledged


async def test_scheduler_recovers_dead_outbox_when_dlq_delivery_is_lost(env):
    enable(env)
    identity = await event(env, "poison")
    await record_failure(env, identity, 0, "unsupported_event", final=True)
    assert not rows(env, "SELECT * FROM cf_dead_letters")
    assert await recover_dead_letters(env) == {"recovered": 1}
    assert await recover_dead_letters(env) == {"recovered": 0}
    letter = rows(env, "SELECT event_id,state FROM cf_dead_letters")[0]
    assert letter == {"event_id": identity, "state": "quarantined"}


async def test_scheduler_uses_business_date_at_utc_midnight_boundary(env):
    enable(env)
    env.BUSINESS_UTC_OFFSET_MINUTES = "480"
    instant = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)
    await tick(env, int(instant.timestamp() * 1000))
    runs = rows(env, "SELECT kind,on_date FROM cf_job_runs")
    assert {r["kind"] for r in runs} == {"quotes", "audit", "overdue"}
    assert {r["on_date"] for r in runs} == {"2026-11-13"}


async def test_quote_expiry_and_audit_retention_use_bounded_native_commands(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    quote = await client.post(
        "/api/sales/quotations",
        headers=headers,
        json={
            "customer_id": party["id"],
            "valid_until": "2030-01-01",
            "lines": [{"description": "Fee", "qty": "1", "unit_price": "10"}],
        },
    )
    assert quote.status_code == 201, quote.text
    identity = quote.json()["id"]
    sent = await client.post(f"/api/sales/quotations/{identity}/send", headers=headers)
    assert sent.status_code == 200, sent.text
    # Simulate passage of time without mutating a confirmed quote.
    result = await run_maintenance(env, "quotes", "future", date(2030, 1, 2))
    assert result["processed"] == 1
    assert rows(env, "SELECT status FROM sales_quotations WHERE id=?", (identity,))[0] == {
        "status": "expired"
    }
    audits = rows(env, "SELECT id FROM core_audit_logs")
    first = audits[0]["id"]
    env.DB.connection.execute(
        "UPDATE core_audit_logs SET created_at='2020-01-01' WHERE id=?", (first,)
    )
    result = await run_maintenance(env, "audit", "retention", date(2026, 11, 13))
    assert result["processed"] == 1
    assert not rows(env, "SELECT id FROM core_audit_logs WHERE id=?", (first,))
    assert len(rows(env, "SELECT id FROM core_audit_logs")) == len(audits) - 1


async def test_overdue_scan_resumes_past_page_boundary_and_excludes_credits(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    for _ in range(26):
        await invoice(client, headers, party, "1", due_date="2020-01-01")
    first = await run_maintenance(env, "overdue", "pages", date.today(), pages=1)
    assert first["status"] == "running" and first["processed"] == 25
    second = await run_maintenance(env, "overdue", "pages", date.today(), pages=1)
    assert second["status"] == "complete" and second["processed"] == 26
    await drain(env)
    assert len(rows(env, "SELECT * FROM core_notifications WHERE type='invoice_overdue'")) == 26
    assert (await run_maintenance(env, "overdue", "pages", date.today())) == {
        "status": "complete",
        "processed": 26,
    }


async def test_native_plans_compete_safely_without_relying_on_coordinator_lock(client, env):
    enable(env)
    headers, _, _, party = await setup(client)
    await invoice(client, headers, party)
    identity = rows(env, "SELECT id FROM cf_outbox")[0]["id"]
    plans = [
        UnitOfWork(D1Database(env.DB), env.ORG_ID, None, str(n), "test.jobs", {}) for n in [1, 2]
    ]
    for plan in plans:
        await consume_event(plan, env, {"event_id": identity, "generation": 0})
    await plans[0].commit({})
    from erp_cf.errors import ConflictError

    with pytest.raises(ConflictError):
        await plans[1].commit({})
    assert len(rows(env, "SELECT * FROM cf_event_receipts")) == 1
    assert len(rows(env, "SELECT * FROM core_notifications")) == 1


async def test_mail_modes_never_silently_drop_live_intents(env):
    enable(env, mode="live")
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, "mail-live", "test", {})
    await Notifications(plan, env).mail(
        "test@example.com", "Subject\r\nInjected", "Body", kind="manual", dedupe="mail"
    )
    await plan.commit({})
    assert rows(env, "SELECT status,subject FROM cf_mail_outbox") == [
        {"status": "pending", "subject": "Subject  Injected"}
    ]
    identity = rows(env, "SELECT id FROM cf_outbox")[0]["id"]
    with pytest.raises(Exception, match="email_unavailable"):
        await system(env, "jobs.event", "live-mail", {"event_id": identity, "generation": 0})
    assert rows(env, "SELECT status FROM cf_mail_outbox")[0]["status"] == "pending"


async def test_tick_access_and_daily_maintenance_metadata(client, env):
    enable(env)
    headers = {
        "Authorization": "Bearer " + await login(client),
        "X-Bootstrap-Token": env.BOOTSTRAP_TOKEN,
    }
    assert (await client.post("/internal/jobs/tick", json={})).status_code == 403
    response = await client.post(
        "/internal/jobs/tick", headers=headers, json={"force_maintenance": True}
    )
    assert response.status_code == 200, response.text
    assert len(rows(env, "SELECT * FROM cf_job_runs")) == 3
    status = await client.get("/api/jobs/status", headers=headers)
    assert status.status_code == 200 and status.json()["scheduler"]["last_success_at"]
    env.ENVIRONMENT = "production"
    assert (await client.post("/internal/jobs/tick", headers=headers, json={})).status_code == 404
    env.JOBS_ENABLED = "false"
    assert await tick(env, int(datetime.now(UTC).timestamp() * 1000)) == {"status": "disabled"}
