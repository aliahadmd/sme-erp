"""Background jobs: registry, inline execution, worker wiring, overdue parity."""


async def test_jobs_registered():
    from app.jobs import job_names

    assert "check_overdue_invoices" in job_names()
    assert "purge_login_counters" in job_names()


async def test_worker_settings_wiring():
    from app.jobs import WorkerSettings

    names = [f.__name__ for f in WorkerSettings.functions]
    assert set(names) >= {
        "check_overdue_invoices",
        "purge_login_counters",
        "generate_missing_descriptions",
        "expire_quotations",
        "worker_heartbeat",
    }
    assert len(WorkerSettings.cron_jobs) == 6  # overdue, counters, backup, retention, heartbeat


async def test_inline_enqueue_runs_job(client):
    """JOBS_MODE=inline executes synchronously at the call site."""
    from app.jobs.queue import enqueue

    admin_token = (
        await client.post(
            "/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}
        )
    ).json()["access_token"]
    customer = (
        await client.post(
            "/api/crm/contacts",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Jobs Buyer", "is_customer": True},
        )
    ).json()
    invoice = (
        await client.post(
            "/api/invoicing/invoices",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "invoice_type": "ar",
                "party_id": customer["id"],
                "due_date": "2020-01-01",  # overdue on arrival
                "lines": [
                    {"product_id": None, "description": "Old", "qty": "1", "unit_price": "9.00"}
                ],
            },
        )
    ).json()
    posted = await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/post",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert posted.status_code == 200, posted.text

    await enqueue("check_overdue_invoices")

    from sqlalchemy import select as _select

    from app.core.db import SessionFactory as _SF
    from app.modules.core.models import Notification as _N

    async with _SF() as dbg:
        rows = (await dbg.scalars(_select(_N))).all()
        from app.modules.core.models import User as _U

        emails = {}
        for n in rows:
            u = await dbg.get(_U, n.user_id)
            emails.setdefault(u.email if u else "?", []).append(n.type)
        print("DBG by user:", {k: len(v) for k, v in emails.items()})

    # Assert directly on the DB for THIS invoice (deterministic regardless of
    # cross-test notification volume).
    from sqlalchemy import select

    from app.core.db import SessionFactory
    from app.modules.core.models import Notification

    async with SessionFactory() as s:
        rows = (
            await s.scalars(
                select(Notification).where(
                    Notification.type == "invoice_overdue",
                    Notification.payload["dedupe"].astext == f"overdue:{invoice['id']}",
                )
            )
        ).all()
    assert rows, "job must create the overdue notification for this invoice"


async def test_unknown_job_raises():
    import pytest

    from app.jobs.queue import enqueue

    with pytest.raises(KeyError):
        await enqueue("nope")
