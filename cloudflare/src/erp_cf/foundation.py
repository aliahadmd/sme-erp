"""Shared fresh-demo defaults; never overwrites existing configuration."""

from app.modules.accounting.defaults import DEFAULT_COA, DEFAULT_MAPPING
from app.modules.core.defaults import DEFAULT_SETTINGS

from erp_cf.errors import PermissionDeniedError


def records(org):
    for code, name, kind in DEFAULT_COA:
        yield (
            "accounting_accounts",
            {"org_id": org["id"], "code": code, "name": name, "type": kind, "is_system": True},
        )
    for key, value in DEFAULT_SETTINGS.items():
        yield "core_settings", {"org_id": org["id"], "key": key, "value": value}
    yield (
        "core_settings",
        {"org_id": org["id"], "key": "accounting.mapping", "value": DEFAULT_MAPPING},
    )
    yield (
        "core_currencies",
        {
            "org_id": org["id"],
            "code": org["base_currency"],
            "name": "US Dollar" if org["base_currency"] == "USD" else org["base_currency"],
        },
    )


async def initialize(plan, actor, payload):
    if not actor.is_superuser:
        raise PermissionDeniedError("Superuser only")
    org = await plan.repository("core_organizations").get(plan.org_id)
    created = {}
    for table, data in records(org):
        field = "key" if table == "core_settings" else "code"
        if not await plan.repository(table).first(f"{field}=?", (data[field],)):
            plan.add(table, {k: v for k, v in data.items() if k != "org_id"})
            created[table] = created.get(table, 0) + 1
    if created:
        plan.audit("initialize", "core.organization", plan.org_id, after=created)
    return {"created": created}
