"""HTTP, system jobs and reset probes must address one organization object."""

from types import SimpleNamespace

import pytest

from erp_cf.background import system
from erp_cf.command_bus import invoke
from erp_cf.command_routing import command_stub
from erp_cf.restart_proof import stub as proof_stub


class Namespace:
    def __init__(self):
        self.calls = []

    def getByName(self, name, options=None):
        self.calls.append((name, options))
        return self

    async def execute(self, envelope):
        return {"status": 201, "body": {"id": "fixture"}}

    async def run_system(self, envelope):
        return {"status": 200, "body": {"ok": True}}


async def test_http_jobs_and_restart_keep_the_same_route_and_reset_retry_policy():
    from starlette.requests import Request

    namespace = Namespace()
    env = SimpleNamespace(
        ORG_ID="organization",
        COMMANDS=namespace,
        COMMAND_COORDINATOR_GENERATION="apac-v1",
        COMMAND_LOCATION_HINT="apac",
    )
    request = Request(
        {
            "type": "http",
            "env": env,
            "headers": [(b"authorization", b"Bearer fixture"), (b"idempotency-key", b"key")],
        }
    )
    assert (await invoke(request, "test", {})).status_code == 201
    assert await system(env, "test", "key", {}) == {"ok": True}
    assert proof_stub(env) is namespace
    assert namespace.calls == [
        ("organization:apac-v1", {"locationHint": "apac"}),
        ("organization:apac-v1", {"locationHint": "apac"}),
        ("organization:apac-v1", {"locationHint": "apac", "retry": {"maxRetries": 0}}),
    ]


def test_existing_route_is_preserved_without_explicit_generation():
    namespace = Namespace()
    env = SimpleNamespace(ORG_ID="organization", COMMANDS=namespace)
    assert command_stub(env) is namespace
    assert namespace.calls == [("organization", None)]


@pytest.mark.parametrize(
    "settings",
    [
        {"COMMAND_COORDINATOR_GENERATION": "per-request/unsafe"},
        {"COMMAND_COORDINATOR_GENERATION": "x" * 33},
        {"COMMAND_LOCATION_HINT": "unknown"},
    ],
)
def test_invalid_route_configuration_fails_before_addressing_an_object(settings):
    namespace = Namespace()
    env = SimpleNamespace(ORG_ID="organization", COMMANDS=namespace, **settings)
    with pytest.raises(ValueError):
        command_stub(env)
    assert not namespace.calls
