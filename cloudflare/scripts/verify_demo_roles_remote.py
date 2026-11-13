"""Verify native role boundaries and HR ownership against the fresh demo.

Uses separate cookie jars and revokes every successful proof session. Forbidden
mutations use valid bodies and must be rejected before committing any record.
No credentials, tokens, addresses, or message content are printed.
"""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://erp-demo-staging-20261114.aliahad.workers.dev"
MATRIX = {
    "admin": {
        200: [
            "/api/org",
            "/api/users",
            "/api/roles",
            "/api/settings",
            "/api/jobs/status",
            "/api/backups",
        ],
    },
    "accountant": {
        200: [
            "/api/invoicing/invoices",
            "/api/invoicing/payments",
            "/api/accounting/trial-balance",
            "/api/users",
        ],
        403: ["/api/roles", "/api/settings", "/api/backups", "/api/hr/employees"],
    },
    "sales": {
        200: [
            "/api/crm/contacts",
            "/api/catalog/products",
            "/api/sales/orders",
            "/api/sales/quotations",
        ],
        403: ["/api/purchasing/orders", "/api/inventory/receipts", "/api/accounting/trial-balance"],
    },
    "purchasing": {
        200: [
            "/api/crm/contacts",
            "/api/catalog/products",
            "/api/purchasing/orders",
            "/api/inventory/receipts",
        ],
        403: ["/api/invoicing/invoices", "/api/sales/orders", "/api/accounting/trial-balance"],
    },
    "warehouse": {
        200: [
            "/api/inventory/stock",
            "/api/inventory/warehouses",
            "/api/inventory/receipts",
            "/api/inventory/deliveries",
            "/api/inventory/adjustments",
        ],
        403: ["/api/invoicing/invoices", "/api/accounting/trial-balance", "/api/settings"],
    },
    "hr": {
        200: [
            "/api/hr/employees",
            "/api/hr/leave-requests",
            "/api/hr/leave-types",
            "/api/hr/departments",
        ],
        403: ["/api/crm/contacts", "/api/invoicing/invoices", "/api/users"],
    },
    "viewer": {
        200: [
            "/api/crm/contacts",
            "/api/catalog/products",
            "/api/sales/orders",
            "/api/purchasing/orders",
            "/api/inventory/stock",
            "/api/invoicing/invoices",
            "/api/accounting/trial-balance",
        ],
        403: ["/api/users", "/api/roles", "/api/hr/employees"],
    },
    "employee": {
        200: ["/api/hr/employees/me", "/api/hr/leave-types", "/api/hr/leave-requests"],
        403: [
            "/api/hr/employees",
            "/api/hr/departments",
            "/api/reports/dashboard",
            "/api/invoicing/invoices",
        ],
    },
    "employee_peer": {
        200: ["/api/hr/employees/me", "/api/hr/leave-types", "/api/hr/leave-requests"],
        403: ["/api/hr/employees", "/api/crm/contacts"],
    },
}


async def main(resume=False, ipv4=False):
    private = json.loads((ROOT / ".demo.demo-staging.json").read_text())
    seed = json.loads((ROOT / ".cloudflare/verification/demo-seed.json").read_text())
    assert seed.get("verified"), "Finish the demo seed before role acceptance"
    completed = seed["completed"]
    artifact = ROOT / ".cloudflare/verification/demo-roles.json"
    results = json.loads(artifact.read_text()) if resume and artifact.exists() else {}

    def save():
        artifact.write_text(json.dumps(results, indent=2) + "\n")

    for role in [*MATRIX, "disabled"]:
        if role in results:
            continue
        credentials = private["admin"] if role == "admin" else private["users"][role]
        async with httpx.AsyncClient(
            base_url=URL,
            timeout=120,
            transport=httpx.AsyncHTTPTransport(
                retries=3, local_address="0.0.0.0" if ipv4 else None
            ),
        ) as client:
            response = await client.post("/api/auth/login", json=credentials)
            if role == "disabled":
                assert response.status_code == 401, "Disabled user was admitted"
                results[role] = {"login_rejected": True}
                save()
                continue
            assert response.status_code == 200, f"{role} login: {response.status_code}"
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            checks = 0

            async def call(
                method, path, body=None, *, expected=200, auth=headers, actor=role, session=client
            ):
                nonlocal checks
                response = await session.request(
                    method,
                    path,
                    json=body,
                    headers={**auth, "Idempotency-Key": "role-check:" + actor + ":" + path},
                )
                assert response.status_code == expected, (
                    f"{actor}: {method} {path}: {response.status_code}"
                )
                checks += 1
                return response.json() if response.content else None

            try:
                me = await call("GET", "/api/auth/me")
                assert me["is_superuser"] == (role == "admin")
                assert ("employee" if role == "employee_peer" else role) in me["roles"]
                for status, paths in MATRIX[role].items():
                    for path in paths:
                        await call("GET", path, expected=status)
                await call("GET", "/api/notifications/unread-count")
                await call("GET", "/api/email-preferences")
                if role in {"viewer", "warehouse", "hr", "employee", "employee_peer", "accountant"}:
                    await call(
                        "POST", "/api/crm/contacts", {"name": "Forbidden role probe"}, expected=403
                    )
                if role in {"sales", "purchasing", "viewer", "warehouse", "hr", "employee"}:
                    await call(
                        "POST",
                        "/api/invoicing/invoices/" + completed["invoice-sale"]["id"] + "/post",
                        expected=403,
                    )
                if role in {"employee", "employee_peer"}:
                    own = await call("GET", "/api/hr/employees/me")
                    requests = await call("GET", "/api/hr/leave-requests")
                    assert requests and all(row["employee_id"] == own["id"] for row in requests)
                    other = completed[
                        "staff-employee_peer" if role == "employee" else "staff-employee"
                    ]
                    first = date.fromisoformat(seed["date"]) + timedelta(days=70)
                    first += timedelta(days=(-first.weekday()) % 7)
                    await call(
                        "POST",
                        "/api/hr/leave-requests",
                        {
                            "employee_id": other["id"],
                            "type_id": completed["annual"]["id"],
                            "date_from": first.isoformat(),
                            "date_to": first.isoformat(),
                        },
                        expected=403,
                    )
                    target = completed[
                        "leave-pending" if role == "employee_peer" else "leave-peer-pending"
                    ]
                    await call(
                        "POST", f"/api/hr/leave-requests/{target['id']}/cancel", expected=403
                    )
                results[role] = {"checks": checks, "permissions_and_ownership": True}
                print(f"PASS native {role}: {checks} role/ownership checks", flush=True)
            finally:
                response = await client.post("/api/auth/logout")
                assert response.status_code == 200, f"{role} session logout failed"
            save()
    print("PASS disabled login rejected; all successful role sessions revoked", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--ipv4", action="store_true", help="Use IPv4 on networks with broken IPv6")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.resume, arguments.ipv4))
