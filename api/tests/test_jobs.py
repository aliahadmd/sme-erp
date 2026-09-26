"""Background jobs: registry, inline execution, worker wiring, overdue parity."""


async def test_jobs_registered():
    from app.jobs import job_names

    assert "check_overdue_invoices" in job_names()
    assert "purge_login_counters" in job_names()


async def test_worker_settings_wiring():
    from app.jobs import WorkerSettings

    names = [f.__name__ for f in WorkerSettings.functions]
    assert set(names) >= {"check_overdue_invoices", "purge_login_counters", "worker_heartbeat"}
    assert len(WorkerSettings.cron_jobs) == 5  # overdue, counters, backup, retention, heartbeat


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
    await client.post(
        f"/api/invoicing/invoices/{invoice['id']}/post",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    await enqueue("check_overdue_invoices")

    page = (
        await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    ).json()
    overdue = [n for n in page["items"] if n["type"] == "invoice_overdue"]
    assert overdue, "job must create overdue notifications"


async def test_unknown_job_raises():
    import pytest

    from app.jobs.queue import enqueue

    with pytest.raises(KeyError):
        await enqueue("nope")
