"""Fresh authorization is shared only inside one HTTP dependency graph."""

import pytest

from erp_cf import identity
from tests.test_auth import login


async def test_http_dependencies_share_one_identity_but_commands_reauthorize(
    client, env, monkeypatch
):
    access = await login(client)
    headers = {"Authorization": "Bearer " + access}
    checks = []
    original = identity.from_token

    async def tracked(environment, token):
        checks.append(environment)
        return await original(environment, token)

    monkeypatch.setattr(identity, "from_token", tracked)
    # The command bus deliberately holds its own fresh authorization boundary.
    monkeypatch.setattr("erp_cf.command_bus.from_token", tracked)
    assert (await client.get("/api/accounting/trial-balance", headers=headers)).status_code == 200
    assert len(checks) == 1, "Router and permission dependency repeated primary identity reads"
    assert (await client.get("/api/accounting/trial-balance", headers=headers)).status_code == 200
    assert len(checks) == 2, "The next HTTP request must read fresh authorization"
    account = env.DB.connection.execute("SELECT id FROM accounting_accounts LIMIT 1").fetchone()[0]
    response = await client.post(
        "/api/accounting/journal-entries",
        headers=headers,
        json={
            "memo": "Authorization boundaries",
            "lines": [
                {"account_id": account, "debit": "0.01"},
                {"account_id": account, "credit": "0.01"},
            ],
        },
    )
    assert response.status_code == 201
    assert len(checks) == 4, "The coordinator must reauthorize independently of HTTP dependencies"
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/accounting/trial-balance", headers=headers)).status_code == 401
    assert len(checks) == 5, "Revocation must take effect on the very next request"


class BatchOnlyIdentity:
    """Allow one native batch; reject reads that would add extra network trips."""

    def __init__(self, binding):
        self.binding = binding
        self.calls = 0

    def prepare(self, sql):
        statement = self.binding.prepare(sql)

        class Prepared:
            def __init__(self, native):
                self.native = native

            def bind(self, *params):
                return Prepared(self.native.bind(*params))

            async def all(self):
                pytest.fail("Identity reads escaped their single atomic D1 read batch")

        return Prepared(statement)

    async def batch(self, statements):
        self.calls += 1
        return await self.binding.batch([statement.native for statement in statements])


async def test_identity_uses_one_fresh_batch_and_current_superuser_status(client, env):
    access = await login(client)
    binding = env.DB
    tracked = BatchOnlyIdentity(binding)
    env.DB = tracked
    try:
        actor = await identity.from_token(env, access)
        assert actor.is_superuser and "core.user.create" in actor.permissions
        assert tracked.calls == 1
        binding.connection.execute("UPDATE core_users SET is_superuser=0")
        binding.connection.execute("DELETE FROM core_user_roles")
        actor = await identity.from_token(env, access)
        assert not actor.is_superuser and not actor.roles and not actor.permissions
        assert tracked.calls == 2
        binding.connection.execute("UPDATE platform_sessions SET revoked_at=1")
        with pytest.raises(identity.AuthenticationError):
            await identity.from_token(env, access)
        assert tracked.calls == 3
    finally:
        env.DB = binding
