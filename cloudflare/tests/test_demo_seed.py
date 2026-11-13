"""A fresh usable ERP demo, including resume after a lost financial response."""

import httpx
import pytest

from erp_cf.app import create_app
from scripts.demo_seed import VERSION, DemoSeed


async def test_fresh_demo_resumes_a_committed_payment_and_reseeding_preserves_financial_state(env):
    transport = httpx.ASGITransport(app=create_app(env))
    lost = False

    async def request(request):
        nonlocal lost
        response = await transport.handle_async_request(request)
        if (
            not lost
            and request.url.path == "/api/invoicing/payments"
            and response.status_code == 201
        ):
            lost = True
            raise httpx.ReadTimeout("Payment committed but its response was lost")
        return response

    credentials = {
        "admin": {"email": "admin@example.com", "password": "admin123"},
        "users": {
            code: {
                "email": code.replace("_", "-") + "@example.com",
                "password": "test-password",
                "full_name": code.title(),
            }
            for code in [
                "accountant",
                "sales",
                "purchasing",
                "warehouse",
                "hr",
                "viewer",
                "employee",
                "employee_peer",
                "disabled",
            ]
        },
    }
    checkpoint = {"version": VERSION, "date": "2026-11-14"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(request), base_url="https://erp.test"
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await DemoSeed(client, credentials, checkpoint).run()
        assert checkpoint["pending"] == "pay-cable"
        result = await DemoSeed(client, credentials, checkpoint).run()
        assert (
            result["journal_balanced"]
            and result["open_ar"] == "543.96"
            and result["open_ap"] == "2372.00"
        )
        db = env.DB.connection

        def counts():
            return tuple(
                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in [
                    "invoicing_invoices",
                    "invoicing_payments",
                    "accounting_journal_entries",
                    "inventory_stock_moves",
                ]
            )

        before = counts()
        assert before[:2] == (10, 5)
        assert len(db.execute("SELECT id FROM core_users").fetchall()) == 10
        assert (await DemoSeed(client, credentials, checkpoint).run()) == result
        assert counts() == before
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM cf_assertions").fetchone()[0] == 0
