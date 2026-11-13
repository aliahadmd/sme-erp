"""Exercise core, CRM, catalog and currency contracts on real Workers/D1."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-migration-staging-20261113.aliahad.workers.dev"


def expect(response, status):
    if response.status_code != status:
        code = response.json().get("error", {}).get("code", "unknown")
        raise AssertionError(
            f"{response.request.method} {response.request.url.path}: "
            f"expected {status}, got {response.status_code} ({code})"
        )
    return response.json() if status != 204 else None


async def verify():
    credentials = json.loads((ROOT / ".demo.app-staging.json").read_text())
    secrets = json.loads((ROOT / ".secrets.app-staging.json").read_text())
    run = uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=URL, timeout=120) as client:
        login = expect(await client.post("/api/auth/login", json=credentials), 200)
        headers = {"Authorization": "Bearer " + login["access_token"]}

        async def send(method, path, **options):
            for attempt in range(3):
                try:
                    return await client.request(method, path, **options)
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.25)

        async def call(method, path, body=None, *, status=200, key=None):
            selected = {**headers}
            if method not in {"GET", "HEAD"}:
                selected["Idempotency-Key"] = key or uuid4().hex
            return expect(await send(method, path, headers=selected, json=body), status)

        initialized = expect(
            await send(
                "POST",
                "/internal/seed-foundation",
                json={},
                headers={
                    **headers,
                    "X-Bootstrap-Token": secrets["BOOTSTRAP_TOKEN"],
                    "Idempotency-Key": "foundation-v1",
                },
            ),
            200,
        )
        assert isinstance(initialized["created"], dict)
        org = await call("GET", "/api/org")
        await call("PATCH", "/api/org", {"name": org["name"]})
        branch = await call(
            "POST", "/api/branches", {"code": "p" + run, "name": "Proof branch"}, status=201
        )
        await call("PATCH", f"/api/branches/{branch['id']}", {"is_active": False})
        roles = await call("GET", "/api/roles")
        assert len([r for r in roles if r["is_system"]]) == 7
        permissions = await call("GET", "/api/permissions")
        assert len(permissions) == 84
        invalid = await client.patch(
            f"/api/roles/{next(r['id'] for r in roles if r['code'] == 'admin')}",
            headers=headers,
            json={"name": "Not allowed"},
        )
        expect(invalid, 422)
        print("PASS organization, branches, seeded role catalog and system-role protection")

        original = await call("GET", "/api/settings/inventory.guard")
        await call("PUT", "/api/settings/inventory.guard", {"value": {"allow_negative": False}})
        await call("PUT", "/api/settings/inventory.guard", {"value": original["value"]})
        expect(
            await client.put("/api/settings/unknown.key", headers=headers, json={"value": {}}), 422
        )
        number = next(r for r in await call("GET", "/api/numbering") if r["entity"] == "contact")
        await call("PUT", "/api/numbering/contact", {"value": {"prefix": "PROOF"}})

        contact_body = {
            "name": "HÅNDEL proof " + run,
            "is_customer": True,
            "credit_limit": "9999999999999999.99",
            "tags": [run],
        }
        contacts = await asyncio.gather(
            *(
                call("POST", "/api/crm/contacts", contact_body, status=201, key="contact-" + run)
                for _ in range(5)
            )
        )
        assert len({r["id"] for r in contacts}) == 1
        contact = contacts[0]
        assert (
            contact["code"].startswith("PROOF-")
            and contact["credit_limit"] == "9999999999999999.99"
        )
        found = expect(
            await client.get(
                "/api/crm/contacts", headers=headers, params={"q": "hånde", "tag": run}
            ),
            200,
        )
        assert [r["id"] for r in found["items"]] == [contact["id"]]
        await call("PUT", "/api/numbering/contact", {"value": {"prefix": number["prefix"]}})
        await call("PATCH", f"/api/crm/contacts/{contact['id']}", {"payment_terms_days": 14})
        distinct = await asyncio.gather(
            *(
                call(
                    "POST",
                    "/api/crm/contacts",
                    {"name": f"Proof {run} {i}", "is_supplier": True},
                    status=201,
                    key=f"distinct-{run}-{i}",
                )
                for i in range(5)
            )
        )
        assert len({r["code"] for r in distinct}) == 5
        print("PASS exact contacts, Unicode search, duplicate suppression and numbering")

        category = await call(
            "POST", "/api/catalog/categories", {"name": "Proof " + run}, status=201
        )
        uom = await call(
            "POST", "/api/catalog/uoms", {"code": "P" + run, "name": "Proof unit"}, status=201
        )
        tax = await call(
            "POST",
            "/api/catalog/taxes",
            {"code": "P" + run, "name": "Proof tax", "rate_pct": "18.125"},
            status=201,
        )
        assert tax["rate_pct"] == "18.12"
        product = await call(
            "POST",
            "/api/catalog/products",
            {
                "name": "Ångström proof " + run,
                "sku": "P" + run,
                "category_id": category["id"],
                "uom_id": uom["id"],
                "sale_tax_id": tax["id"],
                "sale_price": "999999999999.999999",
            },
            status=201,
        )
        read = await call("GET", f"/api/catalog/products/{product['id']}")
        assert read["sale_price"] == "999999999999.999999"
        expect(
            await client.delete(f"/api/catalog/categories/{category['id']}", headers=headers), 409
        )
        found = expect(
            await client.get(
                "/api/catalog/products", headers=headers, params={"q": "ångSTRÖM proof " + run}
            ),
            200,
        )
        assert found["total"] == 1
        await call("PATCH", f"/api/catalog/products/{product['id']}", {"category_id": None})
        await call("POST", f"/api/catalog/products/{product['id']}/archive")
        await call(
            "DELETE", f"/api/catalog/categories/{category['id']}", status=204, key="delete-" + run
        )
        await call(
            "DELETE", f"/api/catalog/categories/{category['id']}", status=204, key="delete-" + run
        )
        print(
            "PASS exact product/tax values, references, Unicode search, archive and DELETE replay"
        )

        currencies = await call("GET", "/api/currencies")
        if not any(r["code"] == "XCF" for r in currencies):
            await call(
                "POST", "/api/currencies", {"code": "XCF", "name": "Proof currency"}, status=201
            )
        rate = await call(
            "PUT",
            "/api/currencies/rates",
            {"currency": "XCF", "rate_date": "2026-11-13", "rate": "9999999999.99999999"},
        )
        assert rate["rate"] == "9999999999.99999999"
        await call("PUT", "/api/email-preferences", {"invoice_sent": False})
        prefs = await call("GET", "/api/email-preferences")
        assert prefs["invoice_sent"] is False
        await call("PUT", "/api/email-preferences", {"invoice_sent": True})
        assert "items" in await call("GET", "/api/notifications")
        assert "count" in await call("GET", "/api/notifications/unread-count")

        role = await call(
            "POST",
            "/api/roles",
            {
                "code": "p_" + run,
                "name": "Proof creator",
                "permission_codes": ["catalog.product.create"],
            },
            status=201,
        )
        staff_credentials = {
            "email": f"proof-{run}@example.com",
            "password": "proof-only-password-123",
        }
        user = await call(
            "POST",
            "/api/users",
            {**staff_credentials, "full_name": "Proof staff", "role_codes": [role["code"]]},
            status=201,
        )
        async with httpx.AsyncClient(base_url=URL, timeout=120) as staff:
            access = expect(await staff.post("/api/auth/login", json=staff_credentials), 200)[
                "access_token"
            ]
            staff_headers = {"Authorization": "Bearer " + access}
            expect(await staff.get("/api/users", headers=staff_headers), 403)
            await call("PATCH", f"/api/roles/{role['id']}", {"permission_codes": []})
            expect(
                await staff.post(
                    "/api/catalog/products", headers=staff_headers, json={"name": "Denied"}
                ),
                403,
            )
            await call("PATCH", f"/api/users/{user['id']}", {"password": "proof-new-password-123"})
            expect(await staff.get("/api/auth/me", headers=staff_headers), 401)
            expect(await staff.post("/api/auth/refresh"), 401)
        await call("PATCH", f"/api/users/{user['id']}", {"is_active": False})
        for row in [contact, *distinct]:
            await call("DELETE", f"/api/crm/contacts/{row['id']}", status=204)
        logs = expect(
            await client.get(
                "/api/audit-logs",
                headers=headers,
                params={"entity_type": "crm.contact", "limit": 100},
            ),
            200,
        )
        assert (
            sum(r["entity_id"] == contact["id"] and r["action"] == "create" for r in logs["items"])
            == 1
        )
        print("PASS currencies, preferences, notices, RBAC revocation, password reset and audits")


if __name__ == "__main__":
    asyncio.run(verify())
