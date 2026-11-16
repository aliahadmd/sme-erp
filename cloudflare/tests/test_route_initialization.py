"""First financial requests must not rebuild framework dependency metadata."""

import httpx
from fastapi import routing

from erp_cf.app import create_app
from erp_cf.db import D1Database
from tests.test_auth import login


async def test_routes_are_initialized_without_io_before_authenticated_requests(env, monkeypatch):
    with monkeypatch.context() as setup:

        def no_io(*args, **kwargs):
            raise AssertionError("Application initialization must not access bindings")

        setup.setattr(D1Database, "prepare", no_io)
        app = create_app(env)

    def no_schema_build(*args, **kwargs):
        raise AssertionError("Request attempted deferred dependency/response setup")

    # This is the observed hotspot in the locked FastAPI version. Public URL
    # resolution is the application interface; the hook detects deferred work.
    monkeypatch.setattr(routing, "_populate_api_route_state", no_schema_build)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://erp.test"
    ) as client:
        assert (await client.get("/readyz")).status_code == 200
        body = {"lines": [{"description": "Service", "qty": "1", "unit_price": "12"}]}
        assert (await client.post("/api/sales/orders", json=body)).status_code == 401
        headers = {"Authorization": "Bearer " + await login(client)}
        party = await client.post(
            "/api/crm/contacts", headers=headers, json={"name": "Route proof", "is_customer": True}
        )
        assert party.status_code == 201
        order = await client.post(
            "/api/sales/orders", headers=headers, json={**body, "customer_id": party.json()["id"]}
        )
        assert order.status_code == 201 and order.json()["total"] == "12.00"
        assert (await client.get("/api/reports/dashboard", headers=headers)).status_code == 200
        assert (await client.post("/api/auth/logout")).status_code == 200
        assert (await client.get("/api/reports/dashboard", headers=headers)).status_code == 401
