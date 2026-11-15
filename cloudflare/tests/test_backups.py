"""Full snapshots, exact isolated recovery and interrupted-controller behavior."""

import json
import sqlite3
import uuid
from types import SimpleNamespace

import httpx
import pytest

from erp_cf import backup_capture
from erp_cf.app import create_app
from erp_cf.backup_format import canonical, page, schema
from erp_cf.backup_state import load
from erp_cf.backups import BackupController
from erp_cf.db import D1Database
from erp_cf.maintenance import acquire, release, state
from tests.backup_fakes import enable_backups
from tests.jobs_fakes import drain, enable
from tests.test_auth import Commands, login
from tests.test_background import rows
from tests.test_inventory import setup
from tests.test_invoicing import invoice, payment


async def request(client, headers):
    key = uuid.uuid4().hex
    response = await client.post("/api/backups", headers={**headers, "Idempotency-Key": key})
    assert response.status_code == 202, response.text
    duplicate = await client.post("/api/backups", headers={**headers, "Idempotency-Key": key})
    assert duplicate.json() == response.json()
    return response.json()["id"]


async def finish(env, identity, *, restore=False):
    table = "platform_restore_runs" if restore else "platform_backup_runs"
    for _ in range(30):
        # Re-create the controller to prove durable state, not instance memory.
        await BackupController(env).advance(steps=100)
        row = await load(D1Database(env.DB), table, identity)
        assert row["status"] != "failed", row
        if row["status"] == "complete":
            return row
    raise AssertionError("Backup/recovery did not make bounded progress")


async def ready(client, env):
    enable_backups(env)
    headers, _, _, party = await setup(client)
    doc = await invoice(
        client,
        headers,
        party,
        due_date="2020-01-01",
        lines=[
            {
                "description": "18-digit backup proof",
                "qty": "10000",
                "unit_price": "999999999999.999999",
            },
        ],
    )
    await payment(client, headers, party, "0.01", target=doc)
    identity = await request(client, headers)
    return headers, doc, await finish(env, identity)


async def restore(client, env, headers, backup):
    response = await client.post(f"/api/backups/{backup['id']}/restore", headers=headers)
    assert response.status_code == 202, response.text
    return await finish(env, response.json()["id"], restore=True)


async def test_full_snapshot_restores_all_exact_rows_and_schema_then_revokes_sessions(client, env):
    headers, doc, backup = await ready(client, env)
    assert not (await state(D1Database(env.DB)))["active"]
    manifest = await backup_capture.committed(env.BACKUPS, backup)
    assert len(manifest["schema"]["tables"]) == 62
    assert len(manifest["pages"]) > 10
    result = await restore(client, env, headers, backup)
    assert json.loads(result["result_json"])["exact_rows_verified"]
    target = env.RESTORE_DB.connection
    assert (
        target.execute(
            "SELECT CAST(total AS TEXT) FROM invoicing_invoices WHERE id=?", (doc["id"],)
        ).fetchone()[0]
        == "999999999999999999"
    )
    assert (
        target.execute(
            "SELECT CAST(amount_paid AS TEXT) FROM invoicing_invoices WHERE id=?", (doc["id"],)
        ).fetchone()[0]
        == "1"
    )
    assert not target.execute("PRAGMA foreign_key_check").fetchall()
    assert (
        target.execute(
            "SELECT COUNT(*) FROM platform_sessions WHERE revoked_at IS NULL"
        ).fetchone()[0]
        == 0
    )
    assert target.execute("SELECT hold_external FROM platform_recovery_policy").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM platform_backup_runs").fetchone()[0] == 0
    assert (
        target.execute(
            "SELECT json_extract(payload_json,'$.recovery_hold') FROM cf_outbox "
            "WHERE completed_at IS NULL"
        ).fetchone()[0]
        == 1
    )
    assert (
        rows(env, "SELECT status FROM invoicing_invoices WHERE id=?", (doc["id"],))[0]["status"]
        == "partial"
    )


async def test_restore_handles_child_before_parent_and_full_int64_rowids(client, env):
    enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    parent = await client.post("/api/catalog/categories", headers=headers, json={"name": "Parent"})
    child = await client.post(
        "/api/catalog/categories",
        headers=headers,
        json={"name": "Child", "parent_id": parent.json()["id"]},
    )
    assert parent.status_code == child.status_code == 201
    env.DB.connection.execute(
        "UPDATE catalog_product_categories SET rowid=-1 WHERE id=?", (child.json()["id"],)
    )
    env.DB.connection.execute(
        "UPDATE catalog_product_categories SET rowid=9007199254740993 WHERE id=?",
        (parent.json()["id"],),
    )
    backup = await finish(env, await request(client, headers))
    await restore(client, env, headers, backup)
    row = env.RESTORE_DB.connection.execute(
        "SELECT parent_id,CAST(rowid AS TEXT) FROM catalog_product_categories WHERE id=?",
        (child.json()["id"],),
    ).fetchone()
    assert tuple(row) == (parent.json()["id"], "-1")
    assert (
        env.RESTORE_DB.connection.execute(
            "SELECT CAST(rowid AS TEXT) FROM catalog_product_categories WHERE id=?",
            (parent.json()["id"],),
        ).fetchone()[0]
        == "9007199254740993"
    )


async def test_lost_r2_put_response_reuses_one_content_addressed_page(client, env):
    controller = enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)
    failed = False

    async def lost(key, value):
        nonlocal failed
        if "/pages/" in key and not failed:
            failed = True
            raise OSError("R2 accepted the write before losing its response")

    env.BACKUPS.after_put = lost
    await controller.advance(steps=100)
    assert failed
    env.BACKUPS.after_put = None
    backup = await finish(env, identity)
    refs = rows(env, "SELECT object_key FROM platform_backup_pages WHERE backup_id=?", (identity,))
    assert len(refs) == len({key for key in env.BACKUPS.objects if "/pages/" in key})
    assert len(refs) == int(backup["page_index"])


async def test_expired_partial_capture_never_publishes_a_commit_marker(client, env):
    controller = enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)
    await controller.advance(steps=2)
    env.DB.connection.execute("UPDATE platform_maintenance SET expires_at=0")
    await BackupController(env).advance()
    row = await load(D1Database(env.DB), "platform_backup_runs", identity)
    assert row["status"] == "failed" and row["last_error"] == "maintenance_lease_lost"
    assert not any("COMMITTED" in key for key in env.BACKUPS.objects)
    assert not (await state(D1Database(env.DB)))["active"]


async def test_lost_target_commit_response_does_not_duplicate_data(client, env):
    headers, _, backup = await ready(client, env)
    native, failed = env.RESTORE_DB.batch, False

    async def lost(statements):
        nonlocal failed
        result = await native(statements)
        if not failed and any(
            s.sql.startswith("INSERT INTO platform_restore_receipts")
            and str(s.params[1]).startswith("data:")
            for s in statements
        ):
            failed = True
            raise OSError("Target committed; response was lost")
        return result

    env.RESTORE_DB.batch = lost
    result = await restore(client, env, headers, backup)
    assert failed and result["status"] == "complete"


async def test_occupied_target_and_accidental_source_binding_are_rejected(client, env):
    headers, doc, backup = await ready(client, env)
    env.RESTORE_DB = env.DB
    before = rows(env, "SELECT * FROM invoicing_invoices")
    response = await client.post(f"/api/backups/{backup['id']}/restore", headers=headers)
    identity = response.json()["id"]
    await BackupController(env).advance()
    row = await load(D1Database(env.DB), "platform_restore_runs", identity)
    assert row["status"] == "failed" and rows(env, "SELECT * FROM invoicing_invoices") == before
    assert before[0]["id"] == doc["id"]


@pytest.mark.parametrize("corrupt", ["manifest", "page"])
async def test_corrupt_backup_is_rejected_without_a_completed_restore(client, env, corrupt):
    headers, _, backup = await ready(client, env)
    if corrupt == "manifest":
        from erp_cf.backup_state import manifest_key

        key = manifest_key(backup)
    else:
        key = rows(env, "SELECT object_key FROM platform_backup_pages LIMIT 1")[0]["object_key"]
    env.BACKUPS.objects[key] = "{}"
    response = await client.post(f"/api/backups/{backup['id']}/restore", headers=headers)
    identity = response.json()["id"]
    for _ in range(5):
        await BackupController(env).advance(steps=100)
    row = await load(D1Database(env.DB), "platform_restore_runs", identity)
    assert row["status"] == "failed"
    assert (
        not env.RESTORE_DB.connection.execute(
            "SELECT name FROM sqlite_schema WHERE name='platform_recovery_policy'"
        ).fetchall()
        if corrupt == "manifest"
        else True
    )


async def test_candidate_manifest_without_seal_is_not_recoverable(client, env):
    controller = enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)

    async def expire_after_candidate(key, value):
        if "/manifest-" in key:
            env.DB.connection.execute("UPDATE platform_maintenance SET expires_at=0")

    env.BACKUPS.after_put = expire_after_candidate
    for _ in range(10):
        await controller.advance(steps=100)
        row = await load(D1Database(env.DB), "platform_backup_runs", identity)
        if row["status"] == "failed":
            break
    assert row["status"] == "failed"
    assert any("/manifest-" in key for key in env.BACKUPS.objects)
    assert not any("COMMITTED" in key for key in env.BACKUPS.objects)


async def test_page_size_is_bounded_before_crossing_worker_boundary(client, env):
    enable_backups(env)
    await login(client)
    # Three individually valid large TEXT values must not become one giant page.
    for _index in range(3):
        env.DB.connection.execute(
            "INSERT INTO core_audit_logs(id,org_id,action,entity_type,after) "
            "VALUES(?,?,?,'test',?)",
            (str(uuid.uuid4()), env.ORG_ID, "test", json.dumps({"value": "x" * 1400000})),
        )
    db = D1Database(env.DB)
    lease = await acquire(db, "bounded-page")
    descriptor = await schema(db, lease)
    table = next(t for t in descriptor["tables"] if t["name"] == "core_audit_logs")
    cursor, collected = None, []
    while True:
        value = await page(db, lease, table, cursor)
        if not value["rows"]:
            break
        assert len(canonical(value).encode()) < 2 * 1024 * 1024
        cursor = value["rows"][-1]["cursor"]
        collected.extend(value["rows"])
    await release(db, lease)
    assert len(collected) >= 4


async def test_dense_pages_reduce_round_trips_and_restore_exactly(client, env):
    headers, doc, _ = await ready(client, env)
    env.DB.connection.executemany(
        "INSERT INTO core_audit_logs(id,org_id,action,entity_type,after) "
        "VALUES(?,?,'capacity','test',?)",
        [
            (
                str(uuid.uuid4()),
                env.ORG_ID,
                json.dumps({"index": index, "label": "精确"}, ensure_ascii=False),
            )
            for index in range(1250)
        ],
    )
    identity = await request(client, headers)
    backup = await finish(env, identity)
    manifest = await backup_capture.committed(env.BACKUPS, backup)
    pages = [ref for ref in manifest["pages"] if ref["table_name"] == "core_audit_logs"]
    assert len(pages) == 3, "Small audit rows should not require dozens of R2/D1 page exchanges"
    assert int(pages[0]["rows"]) == 500
    source = rows(env, "SELECT id,after FROM core_audit_logs ORDER BY id")
    native = env.RESTORE_DB.batch
    batches = []

    async def inspect(statements):
        batches.append(len(statements))
        return await native(statements)

    env.RESTORE_DB.batch = inspect
    result = await restore(client, env, headers, backup)
    assert result["status"] == "complete" and max(batches) >= 501
    target = [
        dict(row)
        for row in env.RESTORE_DB.connection.execute(
            "SELECT id,after FROM core_audit_logs ORDER BY id"
        )
    ]
    assert source == target
    assert (
        env.RESTORE_DB.connection.execute(
            "SELECT CAST(total AS TEXT) FROM invoicing_invoices WHERE id=?", (doc["id"],)
        ).fetchone()[0]
        == "999999999999999999"
    )


async def test_restored_backup_immutability_triggers_are_live(client, env):
    headers, doc, backup = await ready(client, env)
    await restore(client, env, headers, backup)
    with pytest.raises(sqlite3.IntegrityError):
        env.RESTORE_DB.connection.execute("UPDATE accounting_journal_lines SET debit=0")
    original = rows(
        env, "SELECT CAST(total AS TEXT) AS total FROM invoicing_invoices WHERE id=?", (doc["id"],)
    )[0]
    assert original["total"] == "999999999999999999"


async def test_private_archive_rebuilds_lost_source_metadata_and_remains_restorable(client, env):
    headers, _, backup = await ready(client, env)
    env.DB.connection.execute(
        "DELETE FROM platform_backup_pages WHERE backup_id=?", (backup["id"],)
    )
    env.DB.connection.execute("DELETE FROM platform_backup_runs WHERE id=?", (backup["id"],))
    archive = await client.get("/api/backup-archive", headers=headers)
    assert archive.status_code == 200 and {"id": backup["id"]} in archive.json()["items"]
    for _ in range(2):
        imported = await client.post(f"/api/backup-archive/{backup['id']}/import", headers=headers)
        assert imported.status_code == 200, imported.text
    rebuilt = await load(D1Database(env.DB), "platform_backup_runs", backup["id"])
    assert rebuilt["manifest_hash"] == backup["manifest_hash"]
    assert (await restore(client, env, headers, rebuilt))["status"] == "complete"


async def test_cancel_releases_lease_and_audits_once(client, env):
    controller = enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)
    await controller.advance(steps=2)
    assert (await state(D1Database(env.DB)))["active"]
    for _ in range(2):
        response = await client.post(f"/api/backups/{identity}/cancel", headers=headers)
        assert response.status_code == 200, response.text
    assert not (await state(D1Database(env.DB)))["active"]
    assert (
        len(
            rows(
                env,
                "SELECT * FROM core_audit_logs WHERE entity_type='system.backup' "
                "AND action='cancel'",
            )
        )
        == 1
    )
    assert not rows(env, "SELECT * FROM cf_assertions")
    assert not any("COMMITTED" in key for key in env.BACKUPS.objects)


async def test_retention_skips_active_recovery_and_deletes_with_opaque_pagination(client, env):
    headers, _, backup = await ready(client, env)
    env.DB.connection.execute(
        "UPDATE platform_backup_runs SET created_at='2000-01-01',completed_at='2000-01-01' "
        "WHERE id=?",
        (backup["id"],),
    )
    request_restore = await client.post(f"/api/backups/{backup['id']}/restore", headers=headers)
    assert request_restore.status_code == 202
    controller = BackupController(env)
    await controller.retention()
    assert (await load(D1Database(env.DB), "platform_backup_runs", backup["id"]))[
        "status"
    ] == "complete"
    env.DB.connection.execute("UPDATE platform_restore_runs SET status='failed'")
    env.BACKUPS.list_limit = 3
    await controller.retention()
    await controller.advance(steps=100)
    assert (await load(D1Database(env.DB), "platform_backup_runs", backup["id"]))[
        "status"
    ] == "deleted"
    assert not env.BACKUPS.objects and not rows(env, "SELECT * FROM platform_backup_pages")


async def test_role_checks_survive_rpc_and_metadata_does_not_expose_snapshot_contents(client, env):
    headers, _, backup = await ready(client, env)
    listing = await client.get("/api/backups", headers=headers)
    assert "schema_json" not in listing.text and "hashed_password" not in listing.text
    # Demote the authenticated account while retaining settings permission.
    env.DB.connection.execute("UPDATE core_users SET is_superuser=0,_revision=_revision+1")
    for path in [
        "/api/backups",
        "/api/backups/advance",
        f"/api/backups/{backup['id']}/restore",
        f"/api/backup-archive/{backup['id']}/import",
        f"/api/backups/{backup['id']}/cancel",
    ]:
        response = await client.post(path, headers=headers)
        assert response.status_code == 403, (path, response.text)


async def test_recovered_sessions_and_external_delivery_require_review(client, env):
    headers, doc, backup = await ready(client, env)
    await restore(client, env, headers, backup)
    cloned = SimpleNamespace(**vars(env))
    cloned.DB = env.RESTORE_DB
    cloned.COMMANDS = Commands(cloned)
    enable(cloned, mode="live")
    from tests.test_mail import Email

    cloned.EMAIL = Email()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(cloned)), base_url="https://erp.test"
    ) as recovered:
        assert (await recovered.get("/api/auth/me", headers=headers)).status_code == 401
        fresh = {"Authorization": "Bearer " + await login(recovered)}
        # Replayed pre-restore invoice events may recreate in-app notes but must
        # never replay a potentially delivered external email.
        for _ in range(5):
            await drain(cloned)
        assert not cloned.EMAIL.calls
        assert rows(cloned, "SELECT * FROM cf_mail_outbox WHERE status='uncertain'")
        assert (
            await recovered.get("/api/invoicing/invoices/" + doc["id"], headers=fresh)
        ).status_code == 200


async def test_daily_backup_schedule_is_deduplicated_in_business_timezone(client, env):
    from datetime import UTC, datetime

    from erp_cf.backup_schedule import run

    enable_backups(env)
    env.BACKUP_SCHEDULE_ENABLED = "true"
    env.BACKUP_HOUR_LOCAL = "2"
    env.BUSINESS_UTC_OFFSET_MINUTES = "480"
    scheduled = int(datetime(2026, 11, 13, 18, 5, tzinfo=UTC).timestamp() * 1000)
    for _ in range(5):
        await run(env, scheduled)
    jobs = rows(env, "SELECT id,run_key FROM platform_backup_runs")
    assert len(jobs) == 1 and jobs[0]["run_key"] == "daily:2026-11-14"
    assert (await load(D1Database(env.DB), "platform_backup_runs", jobs[0]["id"]))[
        "status"
    ] == "complete"


async def test_invoice_line_positions_remain_numeric_after_lossless_projection(client, env):
    headers, _, _, party = await setup(client)
    doc = await invoice(
        client,
        headers,
        party,
        post=False,
        lines=[
            {"description": f"Line {index}", "qty": "1", "unit_price": "1"} for index in range(12)
        ],
    )
    response = await client.get("/api/invoicing/invoices/" + doc["id"], headers=headers)
    assert response.status_code == 200
    assert [row["description"] for row in response.json()["lines"]] == [
        f"Line {index}" for index in range(12)
    ]


async def test_capture_total_pause_is_bounded_even_while_lease_heartbeats_continue(client, env):
    controller = enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)
    await controller.advance(steps=2)
    env.DB.connection.execute(
        "UPDATE platform_backup_runs SET capture_started_at='2000-01-01T00:00:00+00:00'"
    )
    await controller.advance()
    row = await load(D1Database(env.DB), "platform_backup_runs", identity)
    assert row["status"] == "failed" and row["last_error"].startswith("backup_timeout")
    assert not (await state(D1Database(env.DB)))["active"]


async def test_recovery_binding_change_cannot_redirect_an_existing_job(client, env):
    headers, _, backup = await ready(client, env)
    response = await client.post(f"/api/backups/{backup['id']}/restore", headers=headers)
    identity = response.json()["id"]
    env.RESTORE_DATABASE_ID = str(uuid.uuid4())
    await BackupController(env).advance()
    row = await load(D1Database(env.DB), "platform_restore_runs", identity)
    assert row["status"] == "failed"
    assert not env.RESTORE_DB.connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table'"
    ).fetchall()


async def test_lost_seal_response_recovers_publication_without_reopening_the_snapshot(client, env):
    enable_backups(env)
    headers = {"Authorization": "Bearer " + await login(client)}
    identity = await request(client, headers)
    native, lost = env.DB.batch, False

    async def lose_seal(statements):
        nonlocal lost
        result = await native(statements)
        if not lost and any(
            s.sql.startswith("UPDATE platform_backup_runs") and "publishing" in s.params
            for s in statements
        ):
            lost = True
            raise OSError("Seal committed but response was lost")
        return result

    env.DB.batch = lose_seal
    backup = await finish(env, identity)
    assert lost and backup["status"] == "complete"
    assert not (await state(D1Database(env.DB)))["active"]
    assert (await backup_capture.committed(env.BACKUPS, backup))["backup_id"] == identity


async def test_recovered_ai_stops_before_quota_or_provider_calls(client, env):
    from erp_cf.ai_client import AIClient
    from erp_cf.errors import DomainError

    headers, _, backup = await ready(client, env)
    await restore(client, env, headers, backup)
    recovered = SimpleNamespace(**vars(env))
    recovered.DB = env.RESTORE_DB
    recovered.AI_ENABLED = "true"
    with pytest.raises(DomainError) as error:
        await AIClient(recovered).complete(system="Facts", prompt="Test")
    assert error.value.code == "ai_recovery_hold"
