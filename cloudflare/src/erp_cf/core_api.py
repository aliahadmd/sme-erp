import uuid
from datetime import UTC, datetime

from app.modules.core.defaults import DEFAULT_SETTINGS
from app.modules.core.schemas import (
    AuditLogOut,
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
from app.shared.pagination import Page, PageParamsDep
from fastapi import APIRouter, Depends, Request

from erp_cf.command_bus import invoke
from erp_cf.core_commands import permission_codes, user_out
from erp_cf.db import D1Database
from erp_cf.errors import NotFoundError
from erp_cf.identity import Actor, current_actor, environment, require
from erp_cf.store import Repository

router = APIRouter(prefix="/api", dependencies=[Depends(current_actor)])


def repository(request, name):
    env = environment(request)
    return Repository(D1Database(env.DB), name, str(env.ORG_ID))


@router.get(
    "/org", response_model=OrganizationOut, dependencies=[Depends(require("core.org.read"))]
)
async def org(request: Request):
    return await repository(request, "core_organizations").get(str(environment(request).ORG_ID))


@router.patch("/org", response_model=OrganizationOut)
async def update_org(body: OrganizationUpdateIn, request: Request):
    return await invoke(request, "core.org.update", {"body": body.model_dump(exclude_unset=True)})


@router.get(
    "/branches", response_model=list[BranchOut], dependencies=[Depends(require("core.branch.read"))]
)
async def branches(request: Request):
    return await repository(request, "core_branches").find(order_by="code")


@router.post("/branches", response_model=BranchOut, status_code=201)
async def create_branch(body: BranchIn, request: Request):
    return await invoke(request, "core.branch.create", {"body": body})


@router.patch("/branches/{identity}", response_model=BranchOut)
async def update_branch(identity: uuid.UUID, body: BranchUpdateIn, request: Request):
    return await invoke(
        request, "core.branch.update", {"id": identity, "body": body.model_dump(exclude_unset=True)}
    )


@router.get(
    "/users", response_model=Page[UserOut], dependencies=[Depends(require("core.user.read"))]
)
async def users(params: PageParamsDep, request: Request):
    repo = repository(request, "core_users")
    page = await repo.page(params.limit, params.offset, order_by="email")
    identities = [r["id"] for r in page["items"]]
    by_user = {identity: [] for identity in identities}
    if identities:
        links = await repo.db.all(
            "SELECT ur.user_id,r.code FROM core_user_roles ur "
            "JOIN core_roles r ON r.id=ur.role_id WHERE ur.user_id IN ("
            + ",".join("?" for _ in identities)
            + ") ORDER BY r.code",
            tuple(identities),
        )
        for link in links:
            by_user[link["user_id"]].append(link["code"])
    page["items"] = [user_out(r, by_user[r["id"]]) for r in page["items"]]
    return page


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreateIn, request: Request):
    return await invoke(request, "core.user.create", {"body": body})


@router.patch("/users/{identity}", response_model=UserOut)
async def update_user(identity: uuid.UUID, body: UserUpdateIn, request: Request):
    return await invoke(
        request, "core.user.update", {"id": identity, "body": body.model_dump(exclude_unset=True)}
    )


@router.get(
    "/roles", response_model=list[RoleOut], dependencies=[Depends(require("core.role.read"))]
)
async def roles(request: Request):
    repo = repository(request, "core_roles")
    return [
        {**row, "permission_codes": await permission_codes(repo.db, row["id"])}
        for row in await repo.find(order_by="code")
    ]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, request: Request):
    return await invoke(request, "core.role.create", {"body": body})


@router.patch("/roles/{identity}", response_model=RoleOut)
async def update_role(identity: uuid.UUID, body: RoleUpdateIn, request: Request):
    return await invoke(
        request, "core.role.update", {"id": identity, "body": body.model_dump(exclude_unset=True)}
    )


@router.get("/permissions", dependencies=[Depends(require("core.role.read"))])
async def permissions(request: Request):
    rows = await repository(request, "core_permissions").find(order_by="code")
    return [{key: row[key] for key in ["code", "module", "action", "description"]} for row in rows]


@router.get(
    "/settings",
    response_model=list[SettingOut],
    dependencies=[Depends(require("core.settings.read"))],
)
async def settings(request: Request):
    return await repository(request, "core_settings").find()


@router.get(
    "/settings/{key}",
    response_model=SettingOut,
    dependencies=[Depends(require("core.settings.read"))],
)
async def setting(key: str, request: Request):
    row = await repository(request, "core_settings").first("key=?", (key,))
    if row is None:
        raise NotFoundError(f"Setting {key} not found")
    return row


@router.put("/settings/{key}", response_model=SettingOut)
async def put_setting(key: str, body: SettingIn, request: Request):
    return await invoke(request, "core.settings.update", {"key": key, "body": body})


@router.get("/numbering", dependencies=[Depends(require("core.settings.read"))])
async def numbering(request: Request):
    row = await repository(request, "core_settings").first("key=?", ("numbering.prefixes",))
    overrides = row["value"] if row else {}
    year = datetime.now(UTC).year
    sequences = await repository(request, "core_document_sequences").find("year=?", (year,))
    values = {r["entity"]: r["last_number"] for r in sequences}
    return [
        {
            "entity": entity,
            "prefix": overrides.get(entity, default),
            "next_number": (
                f"{overrides.get(entity, default)}-{year}-{values.get(entity, 0) + 1:04d}"
            ),
        }
        for entity, default in DEFAULT_SETTINGS["numbering.prefixes"].items()
    ]


@router.put("/numbering/{entity}")
async def put_numbering(entity: str, body: SettingIn, request: Request):
    return await invoke(request, "core.numbering.update", {"entity": entity, "body": body})


@router.get("/email-preferences")
async def preferences(actor: Actor = Depends(current_actor)):
    return {
        "invoice_sent": True,
        "payment_received": True,
        "overdue_reminder": True,
        **actor.user["email_prefs"],
    }


@router.put("/email-preferences")
async def put_preferences(body: dict, request: Request):
    return await invoke(request, "core.preferences.update", {"body": body})


@router.get(
    "/audit-logs",
    response_model=Page[AuditLogOut],
)
async def audits(
    params: PageParamsDep,
    request: Request,
    entity_type: str | None = None,
    action: str | None = None,
    user_id: uuid.UUID | None = None,
    actor: Actor = Depends(current_actor),
):
    actor.require("core.audit.read")
    conditions, values = ["1"], []
    for field, value in [("entity_type", entity_type), ("action", action), ("user_id", user_id)]:
        if value:
            conditions.append(field + "=?")
            values.append(str(value))
    page = await repository(request, "core_audit_logs").page(
        params.limit,
        params.offset,
        where=" AND ".join(conditions),
        params=tuple(values),
        order_by="created_at DESC,id DESC",
    )
    if not actor.is_superuser:
        for record in page["items"]:
            record["ip"] = None
    return page


@router.get("/notifications", response_model=Page[NotificationOut])
async def notifications(
    params: PageParamsDep,
    request: Request,
    unread_only: bool = False,
    actor: Actor = Depends(current_actor),
):
    return await repository(request, "core_notifications").page(
        params.limit,
        params.offset,
        where="user_id=?" + (" AND read_at IS NULL" if unread_only else ""),
        params=(actor.id,),
        order_by="created_at DESC,id DESC",
    )


@router.post("/notifications/{identity}/read", response_model=NotificationOut)
async def read_notification(identity: uuid.UUID, request: Request):
    return await invoke(request, "core.notification.read", {"id": identity})


@router.get("/notifications/unread-count")
async def unread_count(request: Request, actor: Actor = Depends(current_actor)):
    # Cron/event processing will add overdue notices through the shared outbox.
    return {
        "count": await repository(request, "core_notifications").count(
            "user_id=? AND read_at IS NULL", (actor.id,)
        )
    }
