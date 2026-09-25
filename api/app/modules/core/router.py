"""Core module API: auth, organization, branches, users, roles, settings,
audit log, notifications."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import (
    REFRESH_TOKEN,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.modules.core.deps import CurrentUser, CurrentUserDep, client_ip, require
from app.modules.core.models import (
    AuditLog,
    Branch,
    Notification,
    Organization,
    Permission,
    Role,
    RolePermission,
    Setting,
    User,
)
from app.modules.core.schemas import (
    AuditLogOut,
    BranchIn,
    BranchOut,
    LoginIn,
    MeOut,
    NotificationOut,
    OrganizationOut,
    OrganizationUpdateIn,
    RoleIn,
    RoleOut,
    SettingIn,
    SettingOut,
    TokenOut,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from app.modules.core.service import (
    authenticate,
    create_user,
    get_organization,
    set_setting,
    snapshot,
    update_user,
    write_audit,
)
from app.shared.pagination import Page, PageParamsDep, paginate

router = APIRouter()

REFRESH_COOKIE = "refresh_token"
USER_SNAPSHOT_FIELDS = [
    "email",
    "full_name",
    "is_active",
    "is_superuser",
    "default_branch_id",
]


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "prod",
        max_age=settings.refresh_token_days * 24 * 3600,
        path="/api/auth",
    )


# --------------------------------------------------------------------- auth
@router.post("/auth/login", response_model=TokenOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    user = await authenticate(session, body.email, body.password)
    await write_audit(
        session,
        actor=user,
        action="login",
        entity_type="core.user",
        entity_id=user.id,
        ip=client_ip(request),
    )
    await session.commit()
    access, _ = create_access_token(str(user.id))
    refresh, _ = create_refresh_token(str(user.id))
    _set_refresh_cookie(response, refresh)
    return TokenOut(access_token=access)


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AuthenticationError("No refresh token")
    payload = decode_token(token, expected_type=REFRESH_TOKEN)
    jti = payload["jti"]
    redis = request.app.state.redis
    if await redis.get(f"auth:denied:{jti}"):
        raise AuthenticationError("Refresh token was revoked")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("Account not found or disabled")
    access, _ = create_access_token(str(user.id))
    # Rotate the refresh token.
    new_refresh, _ = create_refresh_token(str(user.id))
    ttl = payload["exp"] - int(datetime.now(UTC).timestamp())
    await redis.set(f"auth:denied:{jti}", "1", ex=max(ttl, 1))
    _set_refresh_cookie(response, new_refresh)
    return TokenOut(access_token=access)


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
) -> dict[str, str]:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        try:
            payload = decode_token(token, expected_type=REFRESH_TOKEN)
            ttl = payload["exp"] - int(datetime.now(UTC).timestamp())
            await request.app.state.redis.set(f"auth:denied:{payload['jti']}", "1", ex=max(ttl, 1))
        except AuthenticationError:
            pass  # already invalid — nothing to revoke
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return {"status": "ok"}


@router.get("/auth/me", response_model=MeOut)
async def me(user: CurrentUserDep) -> MeOut:
    u = user.user
    return MeOut(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        is_superuser=u.is_superuser,
        roles=[r.code for r in u.roles],
        permissions=user.permissions,
    )


# -------------------------------------------------------------- organization
@router.get(
    "/org",
    response_model=OrganizationOut,
    dependencies=[Depends(require("core.org.read"))],
)
async def get_org(session: AsyncSession = Depends(get_session)) -> Organization:
    return await get_organization(session)


@router.patch("/org", response_model=OrganizationOut)
async def update_org(
    body: OrganizationUpdateIn,
    user: CurrentUser = Depends(require("core.org.update")),
    session: AsyncSession = Depends(get_session),
) -> Organization:
    org = await get_organization(session)
    before = snapshot(org, [f for f in OrganizationUpdateIn.model_fields])
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="core.organization",
        entity_id=org.id,
        before=before,
        after=snapshot(org, [f for f in OrganizationUpdateIn.model_fields]),
    )
    await session.commit()
    await session.refresh(org)
    return org


# ----------------------------------------------------------------- branches
@router.get(
    "/branches",
    response_model=list[BranchOut],
    dependencies=[Depends(require("core.branch.read"))],
)
async def list_branches(session: AsyncSession = Depends(get_session)) -> list[Branch]:
    return list(await session.scalars(select(Branch).order_by(Branch.code)))


@router.post("/branches", response_model=BranchOut)
async def create_branch(
    body: BranchIn,
    user: CurrentUser = Depends(require("core.branch.create")),
    session: AsyncSession = Depends(get_session),
) -> Branch:
    org = await get_organization(session)
    branch = Branch(org_id=org.id, **body.model_dump())
    session.add(branch)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="core.branch",
        after={"code": branch.code},
    )
    await session.commit()
    await session.refresh(branch)
    return branch


# -------------------------------------------------------------------- users
def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        default_branch_id=user.default_branch_id,
        last_login_at=user.last_login_at,
        role_codes=[r.code for r in user.roles],
    )


async def _resolve_role_ids(session: AsyncSession, codes: list[str]) -> list[uuid.UUID]:
    roles = list(await session.scalars(select(Role).where(Role.code.in_(codes))))
    if len(roles) != len(set(codes)):
        raise ValidationError("Unknown role code in list")
    return [r.id for r in roles]


@router.get("/users", response_model=Page[UserOut])
async def list_users(
    params: PageParamsDep,
    _user: CurrentUser = Depends(require("core.user.read")),
    session: AsyncSession = Depends(get_session),
) -> Page[UserOut]:
    stmt = select(User).order_by(User.email)
    rows, total = await paginate(session, stmt, params)
    return Page(
        items=[_user_out(u) for u in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user_endpoint(
    body: UserCreateIn,
    user: CurrentUser = Depends(require("core.user.create")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    role_ids = await _resolve_role_ids(session, body.role_codes)
    new_user = await create_user(
        session,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        is_superuser=body.is_superuser,
        role_ids=role_ids,
        default_branch_id=body.default_branch_id,
    )
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="core.user",
        entity_id=new_user.id,
        after={"email": new_user.email, "roles": body.role_codes},
    )
    await session.commit()
    await session.refresh(new_user)
    return _user_out(new_user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user_endpoint(
    user_id: uuid.UUID,
    body: UserUpdateIn,
    actor: CurrentUser = Depends(require("core.user.update")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    target = await session.get(User, user_id)
    if not target:
        raise NotFoundError("User not found")
    before = snapshot(target, USER_SNAPSHOT_FIELDS)
    data = body.model_dump(exclude_unset=True)
    role_codes = data.pop("role_codes", None)
    password = data.pop("password", None)
    role_ids = (
        await _resolve_role_ids(session, role_codes) if role_codes is not None else None
    )
    updated = await update_user(
        session,
        target,
        full_name=data.get("full_name"),
        is_active=data.get("is_active"),
        password=password,
        default_branch_id=data.get("default_branch_id", ...),
        role_ids=role_ids,
    )
    await write_audit(
        session,
        actor=actor.user,
        action="update",
        entity_type="core.user",
        entity_id=updated.id,
        before=before,
        after=snapshot(updated, USER_SNAPSHOT_FIELDS) | {"roles": [r.code for r in updated.roles]},
    )
    await session.commit()
    await session.refresh(updated)
    return _user_out(updated)


# -------------------------------------------------------------------- roles
@router.get(
    "/roles",
    response_model=list[RoleOut],
    dependencies=[Depends(require("core.role.read"))],
)
async def list_roles(session: AsyncSession = Depends(get_session)) -> list[RoleOut]:
    roles = list(await session.scalars(select(Role).order_by(Role.code)))
    return [
        RoleOut(
            id=r.id,
            code=r.code,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            permission_codes=sorted(p.code for p in r.permissions),
        )
        for r in roles
    ]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleIn,
    actor: CurrentUser = Depends(require("core.role.create")),
    session: AsyncSession = Depends(get_session),
) -> RoleOut:
    existing = await session.scalars(select(Role).where(Role.code == body.code))
    if existing.first():
        raise ValidationError(f"Role code {body.code} already exists")
    permissions = list(
        await session.scalars(select(Permission).where(Permission.code.in_(body.permission_codes)))
    )
    if len(permissions) != len(set(body.permission_codes)):
        raise ValidationError("Unknown permission code in list")
    role = Role(code=body.code, name=body.name, description=body.description, is_system=False)
    session.add(role)
    await session.flush()
    session.add_all([RolePermission(role_id=role.id, permission_id=p.id) for p in permissions])
    await write_audit(
        session,
        actor=actor.user,
        action="create",
        entity_type="core.role",
        entity_id=role.id,
        after={"code": role.code, "permissions": body.permission_codes},
    )
    await session.commit()
    await session.refresh(role)
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permission_codes=sorted(p.code for p in role.permissions),
    )


@router.get("/permissions", dependencies=[Depends(require("core.role.read"))])
async def list_permissions(session: AsyncSession = Depends(get_session)) -> list[dict]:
    perms = list(await session.scalars(select(Permission).order_by(Permission.code)))
    return [
        {"code": p.code, "module": p.module, "action": p.action, "description": p.description}
        for p in perms
    ]


# ----------------------------------------------------------------- settings
@router.get(
    "/settings",
    response_model=list[SettingOut],
    dependencies=[Depends(require("core.settings.read"))],
)
async def list_settings(session: AsyncSession = Depends(get_session)) -> list[Setting]:
    org = await get_organization(session)
    return list(await session.scalars(select(Setting).where(Setting.org_id == org.id)))


@router.get(
    "/settings/{key}",
    response_model=SettingOut,
    dependencies=[Depends(require("core.settings.read"))],
)
async def get_setting_endpoint(key: str, session: AsyncSession = Depends(get_session)) -> Setting:
    org = await get_organization(session)
    result = await session.scalars(
        select(Setting).where(Setting.org_id == org.id, Setting.key == key)
    )
    setting = result.first()
    if not setting:
        raise NotFoundError(f"Setting {key} not found")
    return setting


@router.put("/settings/{key}", response_model=SettingOut)
async def put_setting(
    key: str,
    body: SettingIn,
    actor: CurrentUser = Depends(require("core.settings.update")),
    session: AsyncSession = Depends(get_session),
) -> Setting:
    org = await get_organization(session)
    setting = await set_setting(session, org.id, key, body.value)
    await write_audit(
        session,
        actor=actor.user,
        action="update",
        entity_type="core.setting",
        entity_id=key,
        after=body.value,
    )
    await session.commit()
    await session.refresh(setting)
    return setting


# ---------------------------------------------------------------- audit log
@router.get("/audit-logs", response_model=Page[AuditLogOut])
async def list_audit_logs(
    params: PageParamsDep,
    _user: CurrentUser = Depends(require("core.audit.read")),
    entity_type: str | None = None,
    action: str | None = None,
    user_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    rows, total = await paginate(session, stmt, params)
    return Page(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


# ------------------------------------------------------------ notifications
@router.get("/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    params: PageParamsDep,
    user: CurrentUserDep,
    unread_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> Page[NotificationOut]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc())
    rows, total = await paginate(session, stmt, params)
    return Page(
        items=[NotificationOut.model_validate(n) for n in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    user: CurrentUserDep,
    session: AsyncSession = Depends(get_session),
) -> Notification:
    notification = await session.get(Notification, notification_id)
    if not notification or notification.user_id != user.id:
        raise NotFoundError("Notification not found")
    notification.read_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(notification)
    return notification


@router.get("/notifications/unread-count")
async def unread_count(
    user: CurrentUserDep, session: AsyncSession = Depends(get_session)
) -> dict[str, int]:
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {"count": count or 0}
