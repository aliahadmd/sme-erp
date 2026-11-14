"""The real reset is verified natively; these cases guard HTTP access and stub policy."""

import pytest

from erp_cf.restart_proof import ORIGIN
from tests.test_auth import login


class Probe:
    def __init__(self):
        self.calls = []

    def getByName(self, name, options):
        assert options == {"retry": {"maxRetries": 0}}
        return self

    async def inspect_instance(self, envelope):
        self.calls.append("inspect")
        return {"instance_id": "before"}

    async def restart_instance(self, envelope):
        self.calls.append("restart")
        raise RuntimeError("Expected reset RPC interruption")


@pytest.mark.parametrize(
    "environment,origin,flag",
    [
        ("production", ORIGIN, "true"),
        ("staging", ORIGIN, "false"),
        ("staging", "https://erp-demo-staging-20261114.aliahad.workers.dev", "true"),
    ],
)
async def test_restart_probe_is_hidden_outside_explicit_verification(
    env, client, environment, origin, flag
):
    env.ENVIRONMENT, env.PUBLIC_ORIGIN, env.RESTART_PROOFS_ENABLED = environment, origin, flag
    probe = Probe()
    env.COMMANDS = probe
    for method, suffix in [("GET", "instance"), ("POST", "restart")]:
        assert (
            await client.request(method, "/internal/proofs/coordinator/" + suffix)
        ).status_code == 404
    assert not probe.calls


async def test_restart_probe_requires_authenticated_superuser_and_does_not_retry_reset_rpc(
    env, client
):
    env.PUBLIC_ORIGIN, env.RESTART_PROOFS_ENABLED = ORIGIN, "true"
    probe = Probe()
    env.COMMANDS = probe
    path = "/internal/proofs/coordinator/"
    assert (await client.post(path + "restart")).status_code == 401
    token = await login(client)
    headers = {"Authorization": "Bearer " + token}
    response = await client.get(path + "instance", headers=headers)
    assert response.status_code == 200 and response.json() == {"instance_id": "before"}
    response = await client.post(path + "restart", headers=headers)
    assert response.status_code == 202 and response.json()["status"] == "reset_requested"
    assert probe.calls == ["inspect", "restart"]
    env.DB.connection.execute("UPDATE core_users SET is_superuser=0")
    assert (await client.post(path + "restart", headers=headers)).status_code == 403
    assert probe.calls == ["inspect", "restart"]
