"""Financial correctness at the HTTP, guarded batch and reporting boundaries."""

import asyncio
import sqlite3
import uuid
from datetime import date
from decimal import Decimal

import pytest

from erp_cf.accounting import Ledger
from erp_cf.accounting_reads import JournalReader
from erp_cf.db import D1Database
from erp_cf.errors import ConflictError
from erp_cf.store import UnitOfWork
from tests.test_auth import login


async def setup(client):
    token = await login(client)
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.get("/api/accounting/accounts", headers=headers)
    assert response.status_code == 200, response.text
    accounts = {r["code"]: r for r in response.json()}
    return headers, accounts


def entry(accounts, amount="100.00", entry_date="2026-01-02"):
    return {
        "memo": "Owner contribution",
        "entry_date": entry_date,
        "lines": [
            {"account_id": accounts["1000"]["id"], "debit": amount},
            {"account_id": accounts["3000"]["id"], "credit": amount},
        ],
    }


def counts(env):
    return {
        table: env.DB.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in [
            "accounting_journal_entries",
            "accounting_journal_lines",
            "core_document_sequences",
            "core_audit_logs",
            "cf_commands",
        ]
    }


async def test_exact_reports_exceed_sqlite_sum_range_and_respect_dates(client, env, monkeypatch):
    monkeypatch.setattr(JournalReader, "page_size", 3)
    headers, accounts = await setup(client)
    amount = "9999999999999999.99"
    for index in range(12):
        body = entry(accounts, amount, "2025-12-31" if index < 6 else "2026-01-02")
        response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
        assert response.status_code == 201, response.text
        assert response.json()["lines"][0]["debit"] == amount
    # The same legitimate stored inputs overflow SQLite's integer SUM.
    with pytest.raises(sqlite3.OperationalError, match="integer overflow"):
        env.DB.connection.execute("SELECT SUM(debit) FROM accounting_journal_lines").fetchone()
    tb = (await client.get("/api/accounting/trial-balance", headers=headers)).json()
    cash = next(r for r in tb if r["code"] == "1000")
    assert Decimal(cash["total_debit"]) == Decimal(amount) * 12
    assert sum(Decimal(r["total_debit"]) for r in tb) == sum(Decimal(r["total_credit"]) for r in tb)
    early = (
        await client.get(
            "/api/accounting/trial-balance", headers=headers, params={"as_of": "2025-12-31"}
        )
    ).json()
    assert Decimal(next(r for r in early if r["code"] == "1000")["balance"]) == Decimal(amount) * 6
    ledger = (
        await client.get(
            "/api/accounting/ledger", headers=headers, params={"account_id": cash["account_id"]}
        )
    ).json()
    assert len(ledger) == 12 and Decimal(ledger[-1]["balance"]) == Decimal(cash["balance"])
    assert [r["entry_date"] for r in ledger] == sorted(r["entry_date"] for r in ledger)
    page = (
        await client.get(
            "/api/accounting/journal-entries", headers=headers, params={"limit": 5, "offset": 5}
        )
    ).json()
    assert page["total"] == 12 and len(page["items"]) == 5
    assert page["items"][0]["lines"][0]["account_code"] == "1000"
    assert page["items"][0]["lines"][1]["account_name"] == "Owner's Equity"
    filtered = await client.get(
        "/api/accounting/journal-entries", headers=headers, params={"source_type": "payment"}
    )
    assert filtered.json()["total"] == 0
    assert (
        await client.patch("/api/org", headers=headers, json={"base_currency": "EUR"})
    ).status_code == 422


async def test_invalid_journals_never_reserve_a_number_or_write_books(client, env):
    headers, accounts = await setup(client)
    before = counts(env)
    for amount in ["-1", "10000000000000000.00", "1e99999999", "NaN", "Infinity", "0.004"]:
        body = entry(accounts, amount)
        response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
        assert response.status_code == 422, (amount, response.text)
        assert counts(env) == before
    for lines in [
        [],
        [{"account_id": accounts["1000"]["id"], "debit": "10"}],
        [{"account_id": accounts["1000"]["id"], "debit": "10", "credit": "10"}],
        [{"account_id": str(uuid.uuid7()), "debit": "10"}],
    ]:
        response = await client.post(
            "/api/accounting/journal-entries",
            headers=headers,
            json={"memo": "Invalid", "lines": lines},
        )
        assert response.status_code == 422, response.text
        assert counts(env) == before
    body = entry(accounts, "10.125")
    response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["lines"][0]["debit"] == "10.12"
    assert response.json()["number"].endswith("0001")


async def test_every_journal_batch_boundary_rolls_back_and_stable_retry_succeeds(client, env):
    headers, accounts = await setup(client)
    headers["Idempotency-Key"] = "journal-failure-retry"
    before = counts(env)
    stage, size = None, None

    async def fail(statements):
        nonlocal size
        size = len(statements)
        failure = env.DB.prepare("INSERT INTO cf_assertions(command_id,valid) VALUES ('fail',0)")
        statements.insert(len(statements) if stage is None else stage, failure)

    env.DB.before_batch = fail
    response = await client.post(
        "/api/accounting/journal-entries", headers=headers, json=entry(accounts)
    )
    assert response.status_code == 409, response.text
    assert size > 10
    assert counts(env) == before
    for stage in range(1, size):
        response = await client.post(
            "/api/accounting/journal-entries", headers=headers, json=entry(accounts)
        )
        assert response.status_code == 409, (stage, response.text)
        assert counts(env) == before
    env.DB.before_batch = None
    response = await client.post(
        "/api/accounting/journal-entries", headers=headers, json=entry(accounts)
    )
    assert response.status_code == 201 and response.json()["number"].endswith("0001")


async def test_concurrent_journal_replay_and_changed_input_conflict(client, env):
    headers, accounts = await setup(client)
    headers["Idempotency-Key"] = "one-contribution"
    responses = await asyncio.gather(
        *[
            client.post("/api/accounting/journal-entries", headers=headers, json=entry(accounts))
            for _ in range(6)
        ]
    )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert counts(env)["accounting_journal_entries"] == 1
    changed = await client.post(
        "/api/accounting/journal-entries", headers=headers, json=entry(accounts, "101")
    )
    assert changed.status_code == 409
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/accounting/journal-entries",
                headers={**headers, "Idempotency-Key": f"distinct-{i}"},
                json=entry(accounts),
            )
            for i in range(8)
        ]
    )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["number"] for r in responses}) == 8


async def test_account_guards_protect_system_accounts_and_reject_race(client, env):
    headers, accounts = await setup(client)
    response = await client.patch(
        f"/api/accounting/accounts/{accounts['1000']['id']}",
        headers=headers,
        json={"is_active": False},
    )
    assert response.status_code == 422
    created = await client.post(
        "/api/accounting/accounts",
        headers=headers,
        json={"code": "1099", "name": "Other cash", "type": "asset"},
    )
    assert created.status_code == 201, created.text
    account = created.json()
    duplicate = await client.post(
        "/api/accounting/accounts",
        headers=headers,
        json={"code": "1099", "name": "Other", "type": "asset"},
    )
    assert duplicate.status_code == 422
    body = entry({**accounts, "1000": account})
    before = counts(env)

    async def deactivate(statements):
        env.DB.connection.execute(
            "UPDATE accounting_accounts SET is_active=0,_revision=_revision+1 WHERE id=?",
            (account["id"],),
        )

    env.DB.before_batch = deactivate
    response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
    assert response.status_code == 409 and counts(env) == before
    env.DB.before_batch = None
    response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
    assert response.status_code == 422
    foreign = UnitOfWork(
        D1Database(env.DB), str(uuid.uuid7()), str(uuid.uuid7()), "foreign-account", "test", {}
    )
    row = foreign.add("accounting_accounts", {"code": "F", "name": "Foreign", "type": "asset"})
    await foreign.commit({"created": True})
    response = await client.post(
        "/api/accounting/journal-entries",
        headers=headers,
        json=entry({**accounts, "1000": row}),
    )
    assert response.status_code == 422
    assert (
        await client.get(
            "/api/accounting/ledger", headers=headers, params={"account_id": row["id"]}
        )
    ).status_code == 404


async def test_posted_history_is_immutable_and_reports_freeze_backdated_writes(client, env):
    headers, accounts = await setup(client)
    body = entry(accounts, "1")
    body["lines"] *= 300
    response = await client.post("/api/accounting/journal-entries", headers=headers, json=body)
    assert response.status_code == 201, response.text
    book = await JournalReader(D1Database(env.DB), env.ORG_ID).snapshot()
    later = await client.post(
        "/api/accounting/journal-entries",
        headers=headers,
        json=entry(accounts, "999", "2020-01-01"),
    )
    assert later.status_code == 201
    rows = [r async for r in book.lines()]
    assert len(rows) == 600
    tb = await book.trial_balance()
    assert next(r for r in tb if r.code == "1000").balance == Decimal("300.00")
    fresh = await JournalReader(D1Database(env.DB), env.ORG_ID).snapshot()
    assert next(r for r in await fresh.trial_balance() if r.code == "1000").balance == Decimal(
        "1299.00"
    )
    for sql in [
        "UPDATE accounting_journal_entries SET memo='changed'",
        "DELETE FROM accounting_journal_entries",
        "UPDATE accounting_journal_lines SET debit=0",
        "DELETE FROM accounting_journal_lines",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            env.DB.connection.execute(sql)


async def test_summary_batches_remain_bounded_and_exclude_backdated_interleaving(
    client, env, monkeypatch
):
    headers, accounts = await setup(client)
    body = entry(accounts, "1")
    body["lines"] *= 300
    assert (
        await client.post("/api/accounting/journal-entries", headers=headers, json=body)
    ).status_code == 201
    monkeypatch.setattr(JournalReader, "page_size", 100)
    db = D1Database(env.DB)
    book = await JournalReader(db, env.ORG_ID).snapshot()
    original_batch = db.batch
    pages = []

    async def batch(statements):
        assert len(statements) == 4
        assert all("e.memo" not in statement.sql for statement in statements)
        result = await original_batch(statements)
        assert all(len(part["results"]) <= 100 for part in result)
        pages.append(sum(len(part["results"]) for part in result))
        if len(pages) == 1:
            later = await client.post(
                "/api/accounting/journal-entries",
                headers=headers,
                json=entry(accounts, "999", "2020-01-01"),
            )
            assert later.status_code == 201
        return result

    monkeypatch.setattr(db, "batch", batch)
    rows = await book.trial_balance()
    assert pages == [400, 200]
    assert next(row for row in rows if row.code == "1000").balance == Decimal("300.00")
    assert sum(row.total_debit for row in rows) == sum(row.total_credit for row in rows)
    fresh = await JournalReader(D1Database(env.DB), env.ORG_ID).snapshot()
    assert next(
        row for row in await fresh.trial_balance() if row.code == "1000"
    ).balance == Decimal("1299.00")


async def test_reversal_mirrors_all_original_entries_despite_mapping_changes(client, env):
    headers, accounts = await setup(client)
    actor_id = env.DB.connection.execute("SELECT id FROM core_users").fetchone()[0]
    db = D1Database(env.DB)
    source = str(uuid.uuid7())

    def plan(key):
        return UnitOfWork(db, env.ORG_ID, actor_id, key, "source", {"id": source})

    original = plan("post-source")
    book = Ledger(original)
    custom = await client.post(
        "/api/accounting/accounts",
        headers=headers,
        json={"code": "1098", "name": "Original settlement", "type": "asset"},
    )
    assert custom.status_code == 201
    cash = await book.get_account(custom.json()["id"])
    ar = await book.account("ar")
    for amount in [Decimal("9007199254740993.01"), Decimal("11.25")]:
        await book.post(
            entry_date=date(2026, 1, 1),
            memo="Source",
            source_type="payment",
            source_id=source,
            lines=[(cash, amount, Decimal(0)), (ar, Decimal(0), amount)],
        )
    await original.commit({"created": True})
    change = plan("change-mapping")
    setting = await change.repository("core_settings").first("key='accounting.mapping'")
    change.update("core_settings", setting, {"value": {"cash": "1010", "ar": "2000"}})
    change.update("accounting_accounts", cash, {"is_active": False})
    await change.commit({"changed": True})
    reversal = plan("reverse-source")
    reversed_entry = await Ledger(reversal).reverse(
        source, memo="Reverse", entry_date=date(2026, 2, 1)
    )
    assert reversed_entry["source_type"] == "reversal" and len(reversed_entry["lines"]) == 4
    assert {r["account_id"] for r in reversed_entry["lines"]} == {cash["id"], ar["id"]}
    await reversal.commit({"id": reversed_entry["id"]})
    tb = await (await JournalReader(db, env.ORG_ID).snapshot()).trial_balance()
    assert all(r.balance == Decimal(0) for r in tb)
    with pytest.raises(ConflictError, match="already been reversed"):
        await Ledger(plan("second-reversal")).reverse(source, memo="Again")


async def test_missing_system_account_is_planned_once_with_its_journal(client, env):
    await setup(client)
    actor_id = env.DB.connection.execute("SELECT id FROM core_users").fetchone()[0]
    env.DB.connection.execute("DELETE FROM accounting_accounts WHERE code='4900'")
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, actor_id, "new-account", "test", {})
    book = Ledger(plan)
    gain = await book.account("fx_gain")
    assert (await book.account("fx_gain"))["id"] == gain["id"]
    await book.post(
        entry_date=date(2026, 1, 1),
        memo="Gain",
        source_type="payment",
        lines=[
            (await book.account("cash"), Decimal(1), Decimal(0)),
            (gain, Decimal(0), Decimal(1)),
        ],
    )
    await plan.commit({"posted": True})
    assert (
        env.DB.connection.execute(
            "SELECT COUNT(*) FROM accounting_accounts WHERE code='4900'"
        ).fetchone()[0]
        == 1
    )
