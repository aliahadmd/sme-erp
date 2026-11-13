"""Verify the existing React SPA and reserved API routing on isolated staging."""

import asyncio
import re

import httpx

URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


async def main():
    async with httpx.AsyncClient(
        base_url=URL, timeout=90, transport=httpx.AsyncHTTPTransport(retries=3)
    ) as client:
        root = await client.get("/")
        assert root.status_code == 200 and "text/html" in root.headers.get("content-type", "")
        assert '<div id="root">' in root.text
        for route in [
            "/login",
            "/sales/orders/new",
            "/invoicing/ar",
            "/hr/leave",
            "/settings/roles",
        ]:
            response = await client.get(route, headers={"Sec-Fetch-Mode": "navigate"})
            assert response.status_code == 200 and response.content == root.content, route
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+\.(?:js|css))"', root.text)
        assert any(name.endswith(".js") for name in assets)
        assert any(name.endswith(".css") for name in assets)
        for name in assets:
            response = await client.get(name)
            assert response.status_code == 200 and len(response.content) > 100
            assert "text/html" not in response.headers.get("content-type", "")
            assert "http://localhost:8000" not in response.text, "Frontend must use its own origin"
        print("PASS native React assets, same-origin API configuration and deep-link fallback")
        navigation = {"Accept": "text/html", "Sec-Fetch-Mode": "navigate"}
        for path, status in [
            ("/api", 404),
            ("/api/migration-route-that-does-not-exist", 404),
            ("/internal/migration-route-that-does-not-exist", 404),
            ("/api/crm/contacts", 401),
        ]:
            response = await client.get(path, headers=navigation)
            assert response.status_code == status, (path, response.status_code)
            assert "application/json" in response.headers.get("content-type", ""), path
            assert "error" in response.json(), path
            if path.startswith(("/api/", "/internal/")):
                assert response.headers.get("cache-control") == "no-store", path
        for path in ["/healthz", "/readyz"]:
            response = await client.get(path, headers=navigation)
            assert response.status_code == 200 and response.json()["status"] == "ok", path
        meta = await client.get("/api/meta")
        assert meta.status_code == 200 and meta.json()["environment"] == "staging"
        schema = await client.get("/openapi.json", headers=navigation)
        assert schema.status_code == 200 and "/api/auth/login" in schema.json()["paths"]
        print("PASS API/auth errors, health and OpenAPI stay JSON under browser navigation")


if __name__ == "__main__":
    asyncio.run(main())
