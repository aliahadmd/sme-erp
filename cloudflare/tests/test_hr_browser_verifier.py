"""Verify the native HR probe's interruption recovery against the real local API."""

import importlib
import json
import uuid
from pathlib import Path

import httpx
import pytest

from erp_cf.app import create_app


@pytest.mark.parametrize("lose_user_response", [False, True])
async def test_hr_probe_resumes_setup_and_requires_current_actor_fences(
    env, monkeypatch, tmp_path, lose_user_response
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    verifier = importlib.import_module("verify_browser_hr_remote")
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "CHECKPOINT", tmp_path / "proof/workflow.json")
    monkeypatch.setattr(verifier, "ORG", env.ORG_ID)
    env.EMAIL_MODE = "test"
    (tmp_path / ".demo.app-staging.json").write_text(
        json.dumps({"email": "admin@example.com", "password": "admin123"})
    )

    class ResponseLoss(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app=create_app(env))
            self.dropped = False

        async def handle_async_request(self, request):
            response = await self.inner.handle_async_request(request)
            if (
                lose_user_response
                and not self.dropped
                and request.url.path == "/api/users"
                and request.method == "POST"
                and response.status_code == 201
            ):
                self.dropped = True
                await response.aclose()
                raise httpx.ReadError("Committed setup response lost")
            return response

    proof = {"run": uuid.uuid4().hex[:12], "commands": {}}
    transport = ResponseLoss()
    async with httpx.AsyncClient(
        base_url="https://erp.test", headers={"Origin": "https://erp.test"}, transport=transport
    ) as client:
        probe = verifier.Experiment(proof, client)
        await probe.login()
        try:
            await probe.guard()
            if lose_user_response:
                with pytest.raises(httpx.ReadError, match="setup response lost"):
                    await probe.prepare()
                probe = verifier.Experiment(json.loads(verifier.CHECKPOINT.read_text()), client)
            await probe.prepare()
            proof = probe.proof
            assert transport.dropped is lose_user_response
            users = await probe.read("/api/users")
            assert len([u for u in users["items"] if proof["run"] in u["email"]]) == 2
            employee = proof["accounts"]["employee"]["employee_id"]
            for suffix, date in [("approved", "2031-03-03"), ("pending", "2031-03-04")]:
                request = await probe.command(
                    "request-" + suffix,
                    "/api/hr/leave-requests",
                    {
                        "employee_id": employee,
                        "type_id": proof["leave_type"]["id"],
                        "date_from": date,
                        "date_to": date,
                    },
                )
                if suffix == "approved":
                    await probe.command(
                        "approve",
                        "/api/hr/leave-requests/" + request["id"] + "/approve",
                        None,
                        expected=200,
                    )

            original_read = probe.read

            async def interrupted_read(path):
                if path == "/api/hr/leave-requests":
                    raise httpx.ReadError("Cleanup interrupted after deactivation")
                return await original_read(path)

            monkeypatch.setattr(probe, "read", interrupted_read)
            with pytest.raises(httpx.ReadError, match="Cleanup interrupted"):
                await probe.cleanup()
            assert not proof.get("cleaned")
            # A saved disable response alone must never count as a current fence.
            user_id = proof["accounts"]["employee"]["user_id"]
            changed = await client.patch(
                "/api/users/" + user_id,
                json={"is_active": True},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert changed.status_code == 200
            resumed = verifier.Experiment(json.loads(verifier.CHECKPOINT.read_text()), client)
            with pytest.raises(AssertionError, match="not fenced"):
                await resumed.cleanup()
            assert not resumed.proof.get("cleaned")
            assert not any(n.startswith("cancel-") for n in resumed.proof["commands"])
            changed = await client.patch(
                "/api/users/" + user_id,
                json={"is_active": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert changed.status_code == 200
            await resumed.cleanup()
            assert resumed.proof["cleaned"] and resumed.proof["retained_requests"] == 3
            rows = await resumed.read("/api/hr/leave-requests")
            assert sorted(r["status"] for r in rows) == ["approved", "cancelled", "cancelled"]
            assert all("password" not in a for a in resumed.proof["accounts"].values())
            await resumed.cleanup()
            assert len(await resumed.read("/api/hr/leave-requests")) == 3
            probe = resumed
        finally:
            await probe.logout()
        assert "owned_admin" not in probe.proof
        assert len(probe.proof["revoked_admins"]) == 1


def test_hr_probe_refuses_overlapping_prepare_browser_or_cleanup(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    verifier = importlib.import_module("verify_browser_hr_remote")
    monkeypatch.setattr(verifier, "DIRECTORY", tmp_path)
    with (
        verifier.owned_run(),
        pytest.raises(RuntimeError, match="still running"),
        verifier.owned_run(),
    ):
        pytest.fail("Overlapping mutation step acquired the lock")
    with verifier.owned_run():
        pass
