"""Delivery claims, ambiguous sends, source revalidation and operator recovery."""

import asyncio
import json
import uuid

import pytest

from erp_cf.background import system
from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.mail_commands import prepare
from erp_cf.mail_provider import PERMANENT, payload_hash
from erp_cf.notifications import Notifications
from erp_cf.store import UnitOfWork
from tests.jobs_fakes import deliver, drain, enable
from tests.test_auth import login
from tests.test_background import message, rows
from tests.test_inventory import setup
from tests.test_invoicing import invoice, payment


class Email:
    def __init__(self, *, error=None, result=None):
        self.calls = []
        self.error, self.result = error, result

    async def send(self, payload):
        self.calls.append(payload)
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return self.result if self.result is not None else {"messageId": "provider-proof-id"}


def provider_error(code):
    error = RuntimeError("Sensitive provider detail must not be logged")
    error.code = code
    return error


async def pending(client, env, *, mode="live", kind="manual", preference=None):
    enable(env, mode=mode)
    env.EMAIL = Email()
    headers, _, _, party = await setup(client)
    response = await client.patch(
        "/api/crm/contacts/" + party["id"],
        headers=headers,
        json={"emails": [{"value": "customer@example.com"}]},
    )
    assert response.status_code == 200, response.text
    doc = await invoice(client, headers, party, due_date="2020-01-01")
    user = rows(env, "SELECT id,email FROM core_users WHERE is_superuser=1")[0]
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "test.mail", {})
    identity = await Notifications(plan, env).mail(
        user["email"] if kind == "staff" else "customer@example.com",
        "Invoice\r\nsubject",
        "<Hello>&\nSecond line",
        kind=kind,
        source_id=doc["id"],
        dedupe=uuid.uuid4().hex,
        user_id=user["id"] if kind == "staff" else None,
        preference=preference,
    )
    await plan.commit({})
    mail = rows(env, "SELECT * FROM cf_mail_outbox WHERE id=?", (identity,))[0]
    return headers, doc, party, mail


def current(env, mail):
    return rows(env, "SELECT * FROM cf_mail_outbox WHERE id=?", (mail["id"],))[0]


def envelope(mail):
    return {"event_id": mail["delivery_event_id"], "generation": 0, "attempt_id": str(uuid.uuid4())}


async def claim(env, mail):
    payload = envelope(mail)
    result = await system(env, "mail.prepare", uuid.uuid4().hex, payload)
    assert result["action"] == "send", result
    return payload


async def finish(env, payload, *, provider="late-provider-id"):
    return await system(
        env,
        "mail.finish",
        uuid.uuid4().hex,
        {
            **payload,
            "result": {"state": "accepted", "provider_id": provider, "code": None},
        },
    )


def expire(env, mail):
    env.DB.connection.execute(
        "UPDATE cf_mail_outbox SET claim_until='2000-01-01T00:00:00+00:00' WHERE id=?",
        (mail["id"],),
    )


async def resolve(client, headers, mail, action, **fields):
    return await client.post(
        f"/api/jobs/mail/{mail['id']}/resolve",
        headers=headers,
        json={"action": action, "reason": "Verified provider history", **fields},
    )


async def test_one_send_for_concurrent_duplicates_with_escaped_payload_and_private_metadata(
    client, env
):
    headers, _, _, mail = await pending(client, env)
    identity = mail["delivery_event_id"]
    await asyncio.gather(deliver(env, message(env, identity)), deliver(env, message(env, identity)))
    await deliver(env, message(env, identity))
    assert len(env.EMAIL.calls) == 1
    payload = env.EMAIL.calls[0]
    assert payload["subject"] == "Invoice  subject"
    assert "&lt;Hello&gt;&amp;" in payload["html"] and payload["text"] == "<Hello>&\nSecond line"
    assert payload["headers"]["X-ERP-Mail-ID"] == mail["id"]
    row = current(env, mail)
    assert row["status"] == "sent" and row["provider_id"] == "provider-proof-id"
    assert row["completed_at"] and row["claim_until"] is None
    attempts = await client.get(f"/api/jobs/mail/{mail['id']}/attempts", headers=headers)
    assert attempts.status_code == 200 and attempts.json()["total"] == 1
    assert attempts.json()["items"][0]["payload_hash"] == payload_hash(payload)
    listing = await client.get("/api/jobs/mail?limit=1", headers=headers)
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    for response in [listing, attempts]:
        assert "customer@example.com" not in response.text and "<Hello>" not in response.text


@pytest.mark.parametrize("mode", ["disabled", "test"])
async def test_disabled_and_test_modes_cannot_send(client, env, mode):
    _, _, _, mail = await pending(client, env, mode=mode)
    if mail["delivery_event_id"]:
        await deliver(env, message(env, mail["delivery_event_id"]))
    assert current(env, mail)["status"] == mode and not env.EMAIL.calls
    assert len(rows(env, "SELECT * FROM cf_mail_attempts")) == (mode == "test")


async def test_test_mode_runs_complete_invoice_notification_and_manual_email_pipeline(client, env):
    headers, doc, _, mail = await pending(client, env, mode="test")
    response = await client.post(f"/api/invoicing/invoices/{doc['id']}/send-email", headers=headers)
    assert response.status_code == 200, response.text
    for _ in range(5):
        await drain(env)
    intents = rows(env, "SELECT * FROM cf_mail_outbox")
    assert len(intents) >= 4 and {row["status"] for row in intents} == {"test"}
    assert len(rows(env, "SELECT * FROM cf_mail_attempts")) == len(intents)
    assert not env.EMAIL.calls
    assert not rows(env, "SELECT * FROM cf_outbox WHERE completed_at IS NULL")
    assert current(env, mail)["status"] == "test"


@pytest.mark.parametrize(
    "change", ["void", "address", "inactive", "preferences", "permission", "paid"]
)
async def test_delivery_rechecks_source_recipient_permissions_and_preferences(client, env, change):
    staff = change in {"inactive", "preferences", "permission"}
    headers, doc, party, mail = await pending(
        client,
        env,
        kind="staff" if staff else "overdue" if change == "paid" else "manual",
        preference="invoice_sent" if staff else None,
    )
    if change == "void":
        response = await client.post(f"/api/invoicing/invoices/{doc['id']}/void", headers=headers)
        assert response.status_code == 200, response.text
    elif change == "address":
        response = await client.patch(
            "/api/crm/contacts/" + party["id"],
            headers=headers,
            json={"emails": [{"value": "new@example.com"}]},
        )
        assert response.status_code == 200, response.text
    elif change == "inactive":
        env.DB.connection.execute("UPDATE core_users SET is_active=0,_revision=_revision+1")
    elif change == "preferences":
        env.DB.connection.execute(
            "UPDATE core_users SET email_prefs=?,_revision=_revision+1",
            (json.dumps({"invoice_sent": False}),),
        )
    elif change == "permission":
        env.DB.connection.execute("UPDATE core_users SET is_superuser=0,_revision=_revision+1")
        env.DB.connection.execute("DELETE FROM core_user_roles")
    else:
        await payment(client, headers, party, "100", target=doc)
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert current(env, mail)["status"] == "cancelled" and not env.EMAIL.calls
    assert not rows(env, "SELECT * FROM cf_mail_attempts")


@pytest.mark.parametrize("staff", [False, True])
async def test_overdue_uses_current_exact_balance(client, env, staff):
    headers, doc, party, mail = await pending(
        client,
        env,
        kind="staff" if staff else "overdue",
        preference="overdue_reminder" if staff else None,
    )
    await payment(client, headers, party, "23.45", target=doc)
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert "76.55 USD" in env.EMAIL.calls[0]["text"]


@pytest.mark.parametrize("void", [False, True])
async def test_staff_payment_mail_requires_current_recorded_payment(client, env, void):
    headers, _, party, mail = await pending(
        client, env, kind="staff", preference="payment_received"
    )
    pay = await payment(client, headers, party, "5.00")
    env.DB.connection.execute(
        "UPDATE cf_mail_outbox SET source_id=? WHERE id=?", (pay["id"], mail["id"])
    )
    if void:
        response = await client.post(f"/api/invoicing/payments/{pay['id']}/void", headers=headers)
        assert response.status_code == 200, response.text
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert current(env, mail)["status"] == ("cancelled" if void else "sent")
    assert len(env.EMAIL.calls) == (0 if void else 1)


@pytest.mark.parametrize("code", sorted(PERMANENT))
async def test_definitive_provider_rejections_are_terminal_and_not_retried(client, env, code):
    _, _, _, mail = await pending(client, env)
    env.EMAIL.error = provider_error(code)
    await deliver(env, message(env, mail["delivery_event_id"]))
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert len(env.EMAIL.calls) == 1
    row = current(env, mail)
    assert row["status"] == "dead" and row["last_error"] == code


@pytest.mark.parametrize("code", ["E_RATE_LIMIT_EXCEEDED", "E_DAILY_LIMIT_EXCEEDED"])
async def test_quota_rejection_defers_durably_before_ack_and_resumes_when_due(client, env, code):
    _, _, _, mail = await pending(client, env)
    env.EMAIL.error = provider_error(code)
    first = message(env, mail["delivery_event_id"])
    await deliver(env, first)
    assert first.acknowledged and current(env, mail)["status"] == "pending"
    event = rows(env, "SELECT * FROM cf_outbox WHERE id=?", (mail["delivery_event_id"],))[0]
    assert event["available_at"] and event["completed_at"] is None
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert len(env.EMAIL.calls) == 1
    env.EMAIL.error = None
    env.DB.connection.execute("UPDATE cf_outbox SET available_at=NULL WHERE id=?", (event["id"],))
    await deliver(env, message(env, event["id"]))
    assert len(env.EMAIL.calls) == 2 and current(env, mail)["status"] == "sent"
    assert [
        r["state"] for r in rows(env, "SELECT * FROM cf_mail_attempts ORDER BY created_at")
    ] == ["rejected", "accepted"]


@pytest.mark.parametrize("outcome", ["network", "internal", "delivery", "missing_id", "invalid_id"])
async def test_ambiguous_send_never_automatically_resends(client, env, caplog, outcome):
    _, _, _, mail = await pending(client, env)
    if outcome in {"network", "internal", "delivery"}:
        env.EMAIL.error = (
            OSError("customer@example.com private")
            if outcome == "network"
            else provider_error(
                "E_INTERNAL_SERVER_ERROR" if outcome == "internal" else "E_DELIVERY_FAILED"
            )
        )
    else:
        env.EMAIL.result = {} if outcome == "missing_id" else {"messageId": " "}
    await deliver(env, message(env, mail["delivery_event_id"]))
    await deliver(env, message(env, mail["delivery_event_id"]))
    row = current(env, mail)
    assert len(env.EMAIL.calls) == 1 and row["status"] == "uncertain"
    assert row["completed_at"] is None and row["last_error"] == "send_outcome_unknown"
    assert "customer@example.com" not in caplog.text


async def test_lost_claim_response_and_crashed_attempt_require_deliberate_recovery(
    client, env, monkeypatch
):
    import erp_cf.mail_jobs as jobs

    headers, _, _, mail = await pending(client, env)
    native = jobs.system

    async def lost_response(*args):
        await native(*args)
        raise OSError("Claim response was lost")

    monkeypatch.setattr(jobs, "system", lost_response)
    first = message(env, mail["delivery_event_id"])
    await deliver(env, first)
    assert first.retry_options and not env.EMAIL.calls
    monkeypatch.setattr(jobs, "system", native)
    blocked = await resolve(client, headers, mail, "retry", allow_possible_duplicate=True)
    assert blocked.status_code == 409
    await deliver(env, message(env, mail["delivery_event_id"]))
    expire(env, mail)
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert current(env, mail)["status"] == "uncertain" and not env.EMAIL.calls
    assert rows(env, "SELECT state FROM cf_mail_attempts")[0]["state"] == "uncertain"
    refused = await resolve(client, headers, mail, "retry")
    assert refused.status_code == 422
    response = await resolve(client, headers, mail, "retry", allow_possible_duplicate=True)
    assert response.status_code == 200, response.text
    replacement = current(env, mail)
    assert replacement["delivery_event_id"] != mail["delivery_event_id"]
    await deliver(env, message(env, replacement["delivery_event_id"]))
    assert len(env.EMAIL.calls) == 1 and current(env, mail)["status"] == "sent"


async def test_lost_finish_does_not_resend_and_late_receipt_can_reconcile(
    client, env, monkeypatch, caplog
):
    import erp_cf.mail_jobs as jobs

    _, _, _, mail = await pending(client, env)
    native, attempted = jobs.system, {}

    async def fail_finish(environment, operation, key, payload):
        if operation == "mail.finish":
            attempted.update(payload)
            raise OSError("D1 unavailable")
        return await native(environment, operation, key, payload)

    monkeypatch.setattr(jobs, "system", fail_finish)
    await deliver(env, message(env, mail["delivery_event_id"]))
    monkeypatch.setattr(jobs, "system", native)
    assert "provider-proof-id" in caplog.text and "customer@example.com" not in caplog.text
    await deliver(env, message(env, mail["delivery_event_id"]))
    expire(env, mail)
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert current(env, mail)["status"] == "uncertain" and len(env.EMAIL.calls) == 1
    assert await finish(env, attempted) == {"outcome": "sent"}
    assert current(env, mail)["provider_id"] == "late-provider-id"


@pytest.mark.parametrize("action", ["cancel", "retry", "mark_sent"])
async def test_operator_decision_survives_late_provider_receipt(client, env, action):
    headers, _, _, mail = await pending(client, env)
    payload = await claim(env, mail)
    expire(env, mail)
    response = await resolve(
        client,
        headers,
        mail,
        action,
        allow_possible_duplicate=True,
        provider_id="operator-verified-id",
    )
    assert response.status_code == 200, response.text
    before = current(env, mail)
    assert await finish(env, payload) == {"outcome": "obsolete", "receipt_recorded": True}
    assert current(env, mail) == before
    assert rows(env, "SELECT state,provider_id FROM cf_mail_attempts")[0] == {
        "state": "accepted",
        "provider_id": "late-provider-id",
    }
    await deliver(env, message(env, mail["delivery_event_id"]))
    assert not env.EMAIL.calls
    audit = rows(env, "SELECT after FROM core_audit_logs WHERE entity_type='system.mail'")
    assert audit and json.loads(audit[0]["after"])["action"] == action


async def test_racing_plans_have_one_durable_claim_without_live_mutex(client, env):
    _, _, _, mail = await pending(client, env)
    plans = [
        UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "race", {})
        for _ in range(2)
    ]
    for plan in plans:
        assert (await prepare(plan, env, envelope(mail)))["action"] == "send"
    await plans[0].commit({})
    with pytest.raises(ConflictError):
        await plans[1].commit({})
    assert len(rows(env, "SELECT * FROM cf_mail_attempts")) == 1 and not env.EMAIL.calls


@pytest.mark.parametrize("change", ["recipient", "generation"])
async def test_claim_sql_guard_rejects_changes_after_planning_reads(client, env, change):
    headers, _, party, mail = await pending(client, env)
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "race", {})
    assert (await prepare(plan, env, envelope(mail)))["action"] == "send"
    if change == "recipient":
        response = await client.patch(
            "/api/crm/contacts/" + party["id"],
            headers=headers,
            json={"emails": [{"value": "changed@example.com"}]},
        )
        assert response.status_code == 200
    else:
        env.DB.connection.execute(
            "UPDATE cf_outbox SET delivery_generation=1 WHERE id=?", (mail["delivery_event_id"],)
        )
    with pytest.raises(ConflictError):
        await plan.commit({})
    assert current(env, mail)["status"] == "pending"
    assert not rows(env, "SELECT * FROM cf_mail_attempts") and not env.EMAIL.calls


@pytest.mark.parametrize(
    "body",
    [
        {"action": []},
        {"action": "cancel", "reason": "     "},
        {"action": "retry", "reason": "Provider checked", "allow_possible_duplicate": "true"},
    ],
)
async def test_reconciliation_requires_well_formed_explicit_input(client, env, body):
    headers, _, _, mail = await pending(client, env)
    response = await client.post(f"/api/jobs/mail/{mail['id']}/resolve", headers=headers, json=body)
    assert response.status_code == 422
    assert current(env, mail)["status"] == "pending"


@pytest.mark.parametrize("operation", ["mail.prepare", "mail.finish"])
async def test_claim_and_outcome_receipts_roll_back_at_every_statement(client, env, operation):
    _, _, _, mail = await pending(client, env)
    payload = envelope(mail)
    if operation == "mail.finish":
        payload = await claim(env, mail)
        payload["result"] = {"state": "accepted", "provider_id": "proof", "code": None}
    original, width, fail_at = env.DB.batch, 0, 0
    tables = [
        "cf_mail_outbox",
        "cf_mail_attempts",
        "cf_outbox",
        "cf_commands",
        "cf_assertions",
        "cf_event_receipts",
        "cf_business_epochs",
    ]
    before = {table: rows(env, "SELECT * FROM " + table) for table in tables}

    async def fault(statements):
        nonlocal width
        width = len(statements)
        env.DB.connection.execute("BEGIN")
        try:
            for index, statement in enumerate(statements):
                await statement.all()
                if index == fail_at:
                    raise OSError("Injected interrupted batch")
        finally:
            env.DB.connection.rollback()

    env.DB.batch = fault
    command = {"operation": operation, "key": "fault-proof", "payload": payload}
    try:
        assert (await env.COMMANDS.coordinator.run_system(command))["status"] == 500
        for fail_at in range(1, width):
            assert fail_at < width
            assert (await env.COMMANDS.coordinator.run_system(command))["status"] == 500
            assert {table: rows(env, "SELECT * FROM " + table) for table in tables} == before
    finally:
        env.DB.batch = original
    assert (await env.COMMANDS.coordinator.run_system(command))["status"] == 200


async def test_mail_operations_enforce_permissions_and_org_scope(client, env):
    headers, _, _, mail = await pending(client, env)
    token = await login(client)
    env.DB.connection.execute("UPDATE core_users SET is_superuser=0,_revision=_revision+1")
    env.DB.connection.execute("DELETE FROM core_user_roles")
    reader = {"Authorization": "Bearer " + token}
    for path in ["/api/jobs/mail", f"/api/jobs/mail/{mail['id']}/attempts"]:
        assert (await client.get(path, headers=reader)).status_code == 403
    assert (await resolve(client, reader, mail, "cancel")).status_code == 403
    env.DB.connection.execute("UPDATE core_users SET is_superuser=1,_revision=_revision+1")
    env.DB.connection.execute(
        "UPDATE cf_mail_outbox SET org_id=? WHERE id=?", (str(uuid.uuid4()), mail["id"])
    )
    assert (await client.get("/api/jobs/mail", headers=headers)).json()["total"] == 0
    assert (
        await client.get(f"/api/jobs/mail/{mail['id']}/attempts", headers=headers)
    ).status_code == 404
    assert (await resolve(client, headers, mail, "cancel")).status_code == 404
