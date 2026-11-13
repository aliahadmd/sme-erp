"""Core management commands; responses contain only existing public DTO fields."""

import time

from app.modules.accounting.defaults import DEFAULT_MAPPING
from app.modules.core.defaults import DEFAULT_SETTINGS
from app.modules.core.schemas import (
    BranchIn,
    BranchOut,
    BranchUpdateIn,
    NotificationOut,
    OrganizationOut,
    OrganizationUpdateIn,
    RoleIn,
    RoleOut,
    RoleUpdateIn,
    SettingIn,
    SettingOut,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)

from erp_cf.crypto import hash_password
from erp_cf.db import Statement, assertion
from erp_cf.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from erp_cf.foundation import initialize
from erp_cf.numbering import valid_prefix
from erp_cf.store import now, wire

DOCUMENT_TABLES = (
    "accounting_journal_entries",
    "invoicing_invoices",
    "invoicing_payments",
    "sales_sales_orders",
    "purchasing_purchase_orders",
    "sales_quotations",
)
USER_FIELDS = ("email", "full_name", "is_active", "is_superuser", "default_branch_id")


def snapshot(row, fields):
    return {name: wire(row.get(name)) for name in fields}


async def resolve_codes(plan, table, codes):
    wanted = set(codes)
    rows = [r for r in await plan.repository(table).find() if r["code"] in wanted]
    if len(rows) != len(wanted):
        raise ValidationError("Unknown role or permission code in list")
    for row in rows:
        plan.guard(table, row)
    return rows


def user_out(row, codes):
    return UserOut.model_validate({**row, "role_codes": sorted(codes)})


async def role_codes(db, user_id):
    return [
        r["code"]
        for r in await db.all(
            "SELECT r.code FROM core_roles r JOIN core_user_roles ur ON ur.role_id=r.id "
            "WHERE ur.user_id=? ORDER BY r.code",
            (user_id,),
        )
    ]


async def permission_codes(db, role_id):
    return [
        r["code"]
        for r in await db.all(
            "SELECT p.code FROM core_permissions p "
            "JOIN core_role_permissions rp ON rp.permission_id=p.id "
            "WHERE rp.role_id=? ORDER BY p.code",
            (role_id,),
        )
    ]


async def update_org(plan, actor, payload):
    body = OrganizationUpdateIn.model_validate(payload["body"])
    org = await plan.repository("core_organizations").get(plan.org_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("base_currency") and changes["base_currency"] != org["base_currency"]:
        for name in DOCUMENT_TABLES:
            if await plan.repository(name).first():
                raise ValidationError(
                    "The base currency cannot change once documents exist — "
                    "journals and stock values are denominated in it"
                )
        predicates = [
            f"NOT EXISTS(SELECT 1 FROM {name} WHERE org_id=?)" for name in DOCUMENT_TABLES
        ]
        plan.statements.append(
            assertion(
                plan.identity, " AND ".join(predicates), (plan.org_id,) * len(DOCUMENT_TABLES)
            )
        )
    updated = plan.update("core_organizations", org, changes)
    plan.audit(
        "update",
        "core.organization",
        org["id"],
        before=snapshot(org, OrganizationUpdateIn.model_fields),
        after=snapshot(updated, OrganizationUpdateIn.model_fields),
    )
    return OrganizationOut.model_validate(updated)


async def create_branch(plan, actor, payload):
    data = BranchIn.model_validate(payload["body"]).model_dump()
    data["code"] = data["code"].upper()
    if await plan.repository("core_branches").first("code=?", (data["code"],)):
        raise ConflictError(f"Branch {data['code']} already exists")
    row = plan.add("core_branches", data)
    plan.audit("create", "core.branch", row["id"], after={"code": row["code"]})
    return BranchOut.model_validate(row)


async def update_branch(plan, actor, payload):
    row = await plan.repository("core_branches").get(payload["id"])
    changes = BranchUpdateIn.model_validate(payload["body"]).model_dump(exclude_unset=True)
    updated = plan.update("core_branches", row, changes)
    plan.audit(
        "update",
        "core.branch",
        row["id"],
        before=snapshot(row, changes),
        after=snapshot(updated, changes),
    )
    return BranchOut.model_validate(updated)


async def create_user(plan, actor, payload):
    body = UserCreateIn.model_validate(payload["body"])
    if body.is_superuser and not actor.is_superuser:
        raise PermissionDeniedError("Only a superuser can create a superuser account")
    email = str(body.email).strip().lower()
    if await plan.repository("core_users").first("email=?", (email,)):
        raise ConflictError(f"A user with email {email} already exists")
    roles = await resolve_codes(plan, "core_roles", body.role_codes)
    if body.default_branch_id:
        branch = await plan.repository("core_branches").get(str(body.default_branch_id))
        plan.guard("core_branches", branch)
    row = plan.add(
        "core_users",
        {
            "email": email,
            "full_name": body.full_name,
            "hashed_password": await hash_password(body.password),
            "is_superuser": body.is_superuser,
            "default_branch_id": body.default_branch_id,
        },
    )
    for role in roles:
        plan.add("core_user_roles", {"user_id": row["id"], "role_id": role["id"]})
    plan.audit("create", "core.user", row["id"], after={"email": email, "roles": body.role_codes})
    return user_out(row, body.role_codes)


async def update_user(plan, actor, payload):
    body = UserUpdateIn.model_validate(payload["body"])
    target = await plan.repository("core_users").get(payload["id"])
    if target["is_superuser"] and body.is_active is False:
        if not await plan.repository("core_users").first(
            "id<>? AND is_superuser=1 AND is_active=1", (target["id"],)
        ):
            raise ValidationError("Cannot deactivate the last active superuser")
        plan.statements.append(
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM core_users WHERE id<>? AND is_superuser=1 AND is_active=1)",
                (target["id"],),
            )
        )
    if target["id"] == actor.id and body.is_active is False:
        raise ValidationError("You cannot deactivate your own account")
    changes = {
        k: v
        for k, v in body.model_dump(exclude_unset=True).items()
        if k not in {"password", "role_codes"} and (v is not None or k == "default_branch_id")
    }
    if changes.get("default_branch_id"):
        branch = await plan.repository("core_branches").get(str(changes["default_branch_id"]))
        plan.guard("core_branches", branch)
    codes = await role_codes(plan.db, target["id"])
    if body.role_codes is not None:
        roles = await resolve_codes(plan, "core_roles", body.role_codes)
        for link in await plan.repository("core_user_roles").find("user_id=?", (target["id"],)):
            plan.delete("core_user_roles", link)
        for role in roles:
            plan.add("core_user_roles", {"user_id": target["id"], "role_id": role["id"]})
        codes = body.role_codes
    if body.password is not None:
        changes["hashed_password"] = await hash_password(body.password)
    updated = plan.update("core_users", target, changes)
    if body.password is not None or body.is_active is False:
        plan.statements.append(
            Statement(
                "UPDATE platform_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (int(time.time()), target["id"]),
            )
        )
    plan.audit(
        "update",
        "core.user",
        target["id"],
        before=snapshot(target, USER_FIELDS),
        after=snapshot(updated, USER_FIELDS) | {"roles": sorted(codes)},
    )
    return user_out(updated, codes)


async def create_role(plan, actor, payload):
    body = RoleIn.model_validate(payload["body"])
    if await plan.repository("core_roles").first("code=?", (body.code,)):
        raise ValidationError(f"Role code {body.code} already exists")
    permissions = await resolve_codes(plan, "core_permissions", body.permission_codes)
    row = plan.add(
        "core_roles", {"code": body.code, "name": body.name, "description": body.description}
    )
    for permission in permissions:
        plan.add("core_role_permissions", {"role_id": row["id"], "permission_id": permission["id"]})
    plan.audit(
        "create",
        "core.role",
        row["id"],
        after={"code": body.code, "permissions": body.permission_codes},
    )
    return RoleOut.model_validate({**row, "permission_codes": sorted(set(body.permission_codes))})


async def update_role(plan, actor, payload):
    body = RoleUpdateIn.model_validate(payload["body"])
    row = await plan.repository("core_roles").get(payload["id"])
    if row["is_system"]:
        raise ValidationError("System roles are managed by the seed and cannot be edited")
    before_codes = await permission_codes(plan.db, row["id"])
    codes = before_codes
    if body.permission_codes is not None:
        permissions = await resolve_codes(plan, "core_permissions", body.permission_codes)
        for link in await plan.repository("core_role_permissions").find("role_id=?", (row["id"],)):
            plan.delete("core_role_permissions", link)
        for permission in permissions:
            plan.add(
                "core_role_permissions", {"role_id": row["id"], "permission_id": permission["id"]}
            )
        codes = sorted(set(body.permission_codes))
    changes = {
        k: v
        for k, v in body.model_dump(exclude_unset=True).items()
        if k != "permission_codes" and v is not None
    }
    updated = plan.update("core_roles", row, changes)
    plan.audit(
        "update",
        "core.role",
        row["id"],
        before={"name": row["name"], "permissions": before_codes},
        after={"name": updated["name"], "permissions": codes},
    )
    return RoleOut.model_validate({**updated, "permission_codes": codes})


async def validate_setting(plan, key, value):
    if key not in set(DEFAULT_SETTINGS) | {"accounting.mapping"}:
        raise ValidationError(f"Unknown setting '{key}'")
    if key == "accounting.mapping":
        if set(value) - set(DEFAULT_MAPPING):
            raise ValidationError("Unknown accounting mapping keys")
        accounts = {
            r["code"] for r in await plan.repository("accounting_accounts").find("is_active=1")
        }
        if {str(v) for v in value.values()} - accounts:
            raise ValidationError("Accounts do not exist or are inactive")
    elif key == "numbering.prefixes":
        if set(value) - set(DEFAULT_SETTINGS["numbering.prefixes"]) or not all(
            valid_prefix(v) for v in value.values()
        ):
            raise ValidationError(
                "Prefix must be 1-10 characters, alphanumeric with dashes allowed"
            )
    elif key == "inventory.guard" and (
        set(value) - {"allow_negative"} or not isinstance(value.get("allow_negative", False), bool)
    ):
        raise ValidationError("inventory.guard accepts only a boolean 'allow_negative'")


async def set_setting(plan, key, value):
    row = await plan.repository("core_settings").first("key=?", (key,))
    if row:
        return plan.update("core_settings", row, {"value": value})
    return plan.add("core_settings", {"key": key, "value": value})


async def put_setting(plan, actor, payload):
    body = SettingIn.model_validate(payload["body"])
    await validate_setting(plan, payload["key"], body.value)
    row = await set_setting(plan, payload["key"], body.value)
    plan.audit("update", "core.setting", payload["key"], after=body.value)
    return SettingOut.model_validate(row)


async def put_numbering(plan, actor, payload):
    value = SettingIn.model_validate(payload["body"]).value
    chosen = str(value.get("prefix", "")).strip()
    if not valid_prefix(chosen):
        raise ValidationError("Prefix must be 1-10 characters, alphanumeric with dashes allowed")
    if payload["entity"] not in DEFAULT_SETTINGS["numbering.prefixes"]:
        raise NotFoundError("Unknown numbering entity")
    row = await plan.repository("core_settings").first("key=?", ("numbering.prefixes",))
    await set_setting(
        plan, "numbering.prefixes", {**(row["value"] if row else {}), payload["entity"]: chosen}
    )
    plan.audit("update", "core.numbering", payload["entity"], after={"prefix": chosen})
    return {"entity": payload["entity"], "prefix": chosen}


async def put_preferences(plan, actor, payload):
    target = await plan.repository("core_users").get(actor.id)
    allowed = {"invoice_sent", "payment_received", "overdue_reminder"}
    updates = {k: bool(v) for k, v in payload["body"].items() if k in allowed}
    updated = plan.update(
        "core_users", target, {"email_prefs": {**target["email_prefs"], **updates}}
    )
    return updated["email_prefs"]


async def read_notification(plan, actor, payload):
    row = await plan.repository("core_notifications").get(payload["id"])
    if row["user_id"] != actor.id:
        raise NotFoundError("Notification not found")
    return NotificationOut.model_validate(
        plan.update("core_notifications", row, {"read_at": now()})
    )


COMMANDS = {
    "core.initialize": ("core.settings.update", 200, initialize),
    "core.org.update": ("core.org.update", 200, update_org),
    "core.branch.create": ("core.branch.create", 201, create_branch),
    "core.branch.update": ("core.branch.update", 200, update_branch),
    "core.user.create": ("core.user.create", 201, create_user),
    "core.user.update": ("core.user.update", 200, update_user),
    "core.role.create": ("core.role.create", 201, create_role),
    "core.role.update": ("core.role.update", 200, update_role),
    "core.settings.update": ("core.settings.update", 200, put_setting),
    "core.numbering.update": ("core.settings.update", 200, put_numbering),
    "core.preferences.update": (None, 200, put_preferences),
    "core.notification.read": (None, 200, read_notification),
}
