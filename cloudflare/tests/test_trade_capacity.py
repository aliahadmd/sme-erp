"""Exercise the remote verifier's compensation with the real local ERP API."""

import importlib
import json
import uuid
from pathlib import Path

import httpx
import pytest

from erp_cf.app import create_app


@pytest.mark.parametrize("lose_payment_response", [False, True])
async def test_trade_verifier_reconciles_and_reverses_owned_work(
    env, monkeypatch, tmp_path, lose_payment_response
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    verifier = importlib.import_module("verify_trade_capacity_remote")
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "DIRECTORY", tmp_path / "proof")
    monkeypatch.setattr(verifier, "CHECKPOINT", tmp_path / "proof/measurement.json")
    monkeypatch.setattr(verifier, "ORG", env.ORG_ID)
    env.EMAIL_MODE = "test"
    (tmp_path / ".demo.app-staging.json").write_text(
        json.dumps({"email": "admin@example.com", "password": "admin123"})
    )

    async def native(sql):
        return [dict(row) for row in env.DB.connection.execute(sql).fetchall()]

    monkeypatch.setattr(verifier, "native", native)

    class ResponseLoss(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=create_app(env))
            self.dropped = False

        async def handle_async_request(self, request):
            response = await self.inner.handle_async_request(request)
            if (
                lose_payment_response
                and not self.dropped
                and request.method == "POST"
                and request.url.path == "/api/invoicing/payments"
                and response.status_code == 201
            ):
                self.dropped = True
                await response.aclose()
                raise httpx.ReadError("Committed payment response deliberately lost")
            return response

    transport = ResponseLoss()
    async with httpx.AsyncClient(
        base_url="https://erp.test", headers={"Origin": "https://erp.test"}, transport=transport
    ) as client:
        profile = {"name": "normal", "writers": 4, "cycles_each": 1, "readers": 1, "p95_ms": 5000}
        proof = {
            "run": uuid.uuid4().hex,
            "url": verifier.URL,
            "profiles": [profile],
            "commands": [],
            "measurements": [],
        }
        experiment = verifier.Experiment(proof, client)
        original_read = experiment.read

        async def read(path):
            if path == "/api/operations/health":
                # Local tests cover business effects/fencing; real operational
                # health remains an independent native admission/exit check.
                return {"status": "ok"}
            return await original_read(path)

        monkeypatch.setattr(experiment, "read", read)
        proof["measurement_session"] = await experiment.login()
        await experiment.guard()
        await experiment.setup()
        completed = await experiment.measure(profile)
        assert completed is not lose_payment_response
        if completed:
            await experiment.verify_completed()
            assert proof["completed_workflows_verified"] == 4
        with pytest.raises(AssertionError, match="fenced"):
            await experiment.reconcile()
        await experiment.logout()
        await experiment.login()
        await experiment.cleanup()
        await experiment.logout()
        assert proof["cleaned"] and "owned_session" not in proof
        assert len(proof["revoked_sessions"]) == 2
        assert not await native("SELECT * FROM inventory_stock WHERE qty_on_hand<>0")
        assert not await native("SELECT * FROM invoicing_invoices WHERE status<>'void'")
        assert not await native("SELECT * FROM invoicing_payments WHERE status<>'void'")
        assert not await native("SELECT * FROM sales_sales_orders WHERE status<>'cancelled'")
        payments = [c for c in proof["commands"] if c["kind"] == "payment-create"]
        assert all(c["receipt_verified"] for c in payments)
        assert len(payments) == len(await native("SELECT id FROM invoicing_payments"))
        assert transport.dropped is lose_payment_response
        before_resume = await native("SELECT COUNT(*) AS n FROM accounting_journal_entries")
        resumed = verifier.Experiment(json.loads(verifier.CHECKPOINT.read_text()), client)
        resumed_read = resumed.read

        async def read_resumed(path):
            return (
                {"status": "ok"} if path == "/api/operations/health" else await resumed_read(path)
            )

        monkeypatch.setattr(resumed, "read", read_resumed)
        await resumed.login()
        await resumed.cleanup()
        await resumed.logout()
        assert await native("SELECT COUNT(*) AS n FROM accounting_journal_entries") == before_resume
        assert resumed.proof["cleaned"] and "owned_session" not in resumed.proof
