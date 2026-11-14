"""Exercise operational thresholds against real SQL, leases and private R2 metadata."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from erp_cf import operations
from erp_cf.backup_state import manifest_key, prefix
from erp_cf.db import D1Database
from erp_cf.maintenance import acquire, release
from erp_cf.store import UnitOfWork
from tests.backup_fakes import Bucket
from tests.test_auth import login

CHECKED = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def at(seconds=0):
    return (CHECKED + timedelta(seconds=seconds)).isoformat()


def ready(env, monkeypatch):
    env.OPS_ENABLED = env.JOBS_ENABLED = env.BACKUPS_ENABLED = "true"
    env.EMAIL_MODE = "test"
    env.BACKUPS = Bucket()
    monkeypatch.setattr(operations, "now", lambda: CHECKED)
    connection = env.DB.connection
    connection.execute(
        "INSERT INTO cf_scheduler_state(org_id,last_trigger_at,last_success_at) VALUES (?,?,?)",
        (env.ORG_ID, at(), at()),
    )
    identity = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO platform_backup_runs(id,org_id,run_key,status,fence,manifest_hash,"
        "created_at,updated_at,completed_at) VALUES (?,?,?,'complete',1,?,?,?,?)",
        (identity, env.ORG_ID, identity, "a" * 64, at(-3600), at(-3600), at(-3600)),
    )
    backup = dict(connection.execute("SELECT * FROM platform_backup_runs").fetchone())
    for key in [
        f"commits/{env.ORG_ID}/{identity}.json",
        prefix(backup) + "COMMITTED.json",
        manifest_key(backup),
    ]:
        env.BACKUPS.objects[key] = "{}"
    return backup


def codes(report):
    return {item["code"]: item["severity"] for item in report["issues"]}


async def test_healthy_assessment_is_read_only_and_needs_private_archive_objects(env, monkeypatch):
    backup = ready(env, monkeypatch)
    before = env.DB.connection.total_changes
    report = await operations.health(env)
    assert report["status"] == "ok" and not report["issues"]
    assert report["metrics"]["database_bytes"] > 0
    assert report["metrics"]["backup_objects_present"] is True
    assert env.DB.connection.total_changes == before
    del env.BACKUPS.objects[manifest_key(backup)]
    report = await operations.health(env)
    assert report["status"] == "critical" and "backup_artifact_missing" in codes(report)


async def test_future_quota_deferrals_are_not_stuck_jobs_and_payloads_are_not_exposed(
    env, monkeypatch, capsys
):
    ready(env, monkeypatch)
    private = "private recipient and subject must never appear"
    plan = UnitOfWork(D1Database(env.DB), env.ORG_ID, None, uuid.uuid4().hex, "test.event", {})
    identity = plan.emit("test", {"body": private})
    await plan.commit({})
    env.DB.connection.execute(
        "UPDATE cf_outbox SET created_at=?,available_at=? WHERE id=?",
        (at(-86400), at(86400), identity),
    )
    assert (await operations.health(env))["status"] == "ok"
    env.DB.connection.execute("UPDATE cf_outbox SET available_at=NULL WHERE id=?", (identity,))
    report = await operations.emit_health(env)
    assert codes(report) == {"outbox_pending_stale": "critical"}
    line = capsys.readouterr().out.strip()
    assert private not in line and "payload_json" not in line
    assert json.loads(line)["event"] == "erp_operational_health"
    assert json.loads(line)["status"] == "critical"
    assert json.loads(line)["message"] == "erp_operational_health:critical"
    assert json.loads(line)["level"] == "info"


@pytest.mark.parametrize("percentage,severity", [(79, None), (80, "warning"), (90, "critical")])
async def test_database_headroom_thresholds_are_exact(env, monkeypatch, percentage, severity):
    ready(env, monkeypatch)
    data = await operations.snapshot(env, CHECKED)
    data["database_bytes"] = percentage * 100
    report = await operations.assess(
        env, data, CHECKED, operations.Limits(database_budget_bytes=10000)
    )
    assert codes(report).get("database_headroom") == severity


async def test_recent_backup_failure_and_overdue_snapshot_require_attention(env, monkeypatch):
    ready(env, monkeypatch)
    identity = str(uuid.uuid4())
    env.DB.connection.execute(
        "INSERT INTO platform_backup_runs(id,org_id,run_key,status,created_at,updated_at) "
        "VALUES (?,?,?,'failed',?,?)",
        (identity, env.ORG_ID, identity, at(-60), at(-60)),
    )
    assert codes(await operations.health(env)) == {"backup_failed": "warning"}
    env.DB.connection.execute(
        "UPDATE platform_backup_runs SET completed_at=? WHERE status='complete'", (at(-27 * 3600),)
    )
    assert codes(await operations.health(env)) == {
        "backup_failed": "warning",
        "backup_overdue": "critical",
    }


async def test_backup_failure_after_success_in_the_same_second_is_visible(env, monkeypatch):
    ready(env, monkeypatch)
    env.DB.connection.execute(
        "UPDATE platform_backup_runs SET created_at=?,updated_at=?,completed_at=?",
        (at(-7200), at(-3600.9), at(-3600.9)),
    )
    identity = str(uuid.uuid4())
    env.DB.connection.execute(
        "INSERT INTO platform_backup_runs(id,org_id,run_key,status,created_at,updated_at) "
        "VALUES (?,?,?,'failed',?,?)",
        (identity, env.ORG_ID, identity, at(-3600.5), at(-3600.4)),
    )
    assert codes(await operations.health(env)) == {"backup_failed": "warning"}


async def test_expected_write_pause_suppresses_job_age_but_an_overlong_pause_does_not(
    env, monkeypatch
):
    ready(env, monkeypatch)
    identity = str(uuid.uuid4())
    env.DB.connection.execute("UPDATE cf_scheduler_state SET last_success_at=?", (at(-1800),))
    assert "scheduler_stale" in codes(await operations.health(env))
    env.DB.connection.execute(
        "INSERT INTO platform_backup_runs(id,org_id,run_key,status,created_at,"
        "capture_started_at,updated_at) "
        "VALUES (?,?,?,'capturing',?,?,?)",
        (identity, env.ORG_ID, identity, at(-30), at(-30), at()),
    )
    lease = await acquire(D1Database(env.DB), "operations-test")
    try:
        assert (await operations.health(env))["status"] == "maintenance"
        env.DB.connection.execute(
            "UPDATE platform_backup_runs SET capture_started_at=? WHERE id=?", (at(-700), identity)
        )
        report = await operations.health(env)
        assert {"maintenance_overdue", "scheduler_stale"} <= codes(report).keys()
    finally:
        await release(D1Database(env.DB), lease)


async def test_active_mail_claim_is_not_uncertain_but_expired_claim_is_reviewable(env, monkeypatch):
    ready(env, monkeypatch)
    identity = uuid.uuid4().hex * 2
    env.DB.connection.execute(
        "INSERT INTO cf_mail_outbox(id,org_id,recipient,subject,body,kind,status,"
        "created_at,claim_until) "
        "VALUES (?,?,?,?,?,?,'uncertain',?,?)",
        (
            identity,
            env.ORG_ID,
            "private@example.test",
            "private subject",
            "private body",
            "test",
            at(),
            at(60),
        ),
    )
    assert (await operations.health(env))["status"] == "ok"
    env.DB.connection.execute("UPDATE cf_mail_outbox SET claim_until=?", (at(-1),))
    report = await operations.health(env)
    assert codes(report) == {"mail_uncertain": "warning"}
    assert "private" not in json.dumps(report)


async def test_bad_config_missing_size_and_source_failure_never_report_healthy(env, monkeypatch):
    ready(env, monkeypatch)
    env.OPS_BACKUP_AGE_SECONDS = "invalid-secret-value"
    assert (await operations.health(env))["status"] == "unavailable"
    del env.OPS_BACKUP_AGE_SECONDS
    data = await operations.snapshot(env, CHECKED)
    data["database_bytes"] = None
    report = await operations.assess(env, data, CHECKED, operations.Limits())
    assert codes(report) == {"database_size_unavailable": "critical"}

    async def unavailable(*args):
        raise RuntimeError("Private internal database detail")

    monkeypatch.setattr(operations, "snapshot", unavailable)
    report = await operations.health(env)
    assert report["status"] == "unavailable" and "Private" not in json.dumps(report)


async def test_restore_progress_and_failure_are_not_mistaken_for_success(env, monkeypatch):
    backup = ready(env, monkeypatch)
    identity = str(uuid.uuid4())
    env.DB.connection.execute(
        "INSERT INTO platform_restore_runs(id,org_id,backup_id,target_database_id,status,"
        "created_at,updated_at) VALUES (?,?,?,?,'restoring',?,?)",
        (identity, env.ORG_ID, backup["id"], str(uuid.uuid4()), at(-7200), at(-10)),
    )
    assert (await operations.health(env))["status"] == "ok"
    env.DB.connection.execute("UPDATE platform_restore_runs SET updated_at=?", (at(-1900),))
    assert codes(await operations.health(env)) == {"restore_stalled": "critical"}
    env.DB.connection.execute("UPDATE platform_restore_runs SET status='failed'")
    assert codes(await operations.health(env)) == {"restore_failed": "warning"}


async def test_operations_endpoint_requires_current_settings_permission(client, env, monkeypatch):
    ready(env, monkeypatch)
    path = "/api/operations/health"
    assert (await client.get(path)).status_code == 401
    headers = {"Authorization": "Bearer " + await login(client)}
    response = await client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "ok"
    env.DB.connection.execute("UPDATE core_users SET is_superuser=0")
    env.DB.connection.execute("DELETE FROM core_user_roles")
    assert (await client.get(path, headers=headers)).status_code == 403
