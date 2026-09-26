"""Core services: auth, users, roles, settings, audit, notifications."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.modules.core.models import (
    AuditLog,
    Notification,
    Organization,
    Permission,
    Role,
    Setting,
    User,
)

logger = get_logger(__name__)


# --------------------------------------------------------------------- users
async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.scalars(select(User).where(User.email == email.strip().lower()))
    return result.first()


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(session, email)
    if not user or not verify_password(user.hashed_password, password):
        raise AuthenticationError("Invalid email or password")
    if not user.is_active:
        raise AuthenticationError("This account is disabled")
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return user


async def get_user_permissions(session: AsyncSession, user: User) -> list[str]:
    if user.is_superuser:
        result = await session.scalars(select(Permission.code))
        return list(result)
    codes: set[str] = set()
    for role in user.roles:
        codes.update(p.code for p in role.permissions)
    return sorted(codes)


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    is_superuser: bool = False,
    role_ids: list[uuid.UUID] | None = None,
    default_branch_id: uuid.UUID | None = None,
) -> User:
    if await get_user_by_email(session, email):
        raise ConflictError(f"A user with email {email} already exists")
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters")
    roles: list[Role] = []
    if role_ids:
        roles = list(await session.scalars(select(Role).where(Role.id.in_(role_ids))))
        if len(roles) != len(set(role_ids)):
            raise ValidationError("Unknown role in role list")
    user = User(
        email=email.strip().lower(),
        hashed_password=hash_password(password),
        full_name=full_name,
        is_superuser=is_superuser,
        default_branch_id=default_branch_id,
    )
    # Assign while the object is still pending — after flush the collection
    # assignment would trigger a sync lazy-load (MissingGreenlet on asyncpg).
    user.roles = roles
    session.add(user)
    await session.flush()
    return user


async def update_user(
    session: AsyncSession,
    user: User,
    *,
    full_name: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    role_ids: list[uuid.UUID] | None = None,
    default_branch_id: uuid.UUID | None = ...,  # sentinel: None clears, omitted keeps
) -> User:
    if full_name is not None:
        user.full_name = full_name
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters")
        user.hashed_password = hash_password(password)
    if default_branch_id is not ...:
        user.default_branch_id = default_branch_id
    if role_ids is not None:
        roles = list(await session.scalars(select(Role).where(Role.id.in_(role_ids))))
        if len(roles) != len(set(role_ids)):
            raise ValidationError("Unknown role in role list")
        user.roles = roles
    await session.flush()
    return user


# -------------------------------------------------------------------- roles
def snapshot(obj: Any, fields: list[str]) -> dict[str, Any]:
    """Plain-dict snapshot of an ORM object for audit before/after."""
    out: dict[str, Any] = {}
    for f in fields:
        value = getattr(obj, f, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[f] = value
    return out


# --------------------------------------------------------------------- audit
async def write_audit(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        org_id=org_id,
        user_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        before=before,
        after=after,
        ip=ip,
    )
    session.add(entry)
    await session.flush()
    return entry


# ----------------------------------------------------------------- settings
DEFAULT_SETTINGS: dict[str, dict] = {
    "company.info": {"legal_name": "", "tax_id": "", "email": "", "phone": ""},
    "numbering.prefixes": {
        "contact": "C",
        "quotation": "QT",
        "sales_order": "SO",
        "purchase_order": "PO",
        "receipt": "RCV",
        "delivery": "DLV",
        "adjustment": "ADJ",
        "ar_invoice": "INV",
        "ap_invoice": "BILL",
        "customer_payment": "PAY",
        "supplier_payment": "SPAY",
        "journal_entry": "JE",
    },
    "invoicing.defaults": {"payment_terms_days": 30, "default_sale_tax": None},
    "inventory.guard": {"allow_negative": False},
}


async def get_setting(session: AsyncSession, org_id: uuid.UUID, key: str) -> dict | None:
    result = await session.scalars(
        select(Setting).where(Setting.org_id == org_id, Setting.key == key)
    )
    setting = result.first()
    return setting.value if setting else None


async def set_setting(session: AsyncSession, org_id: uuid.UUID, key: str, value: dict) -> Setting:
    result = await session.scalars(
        select(Setting).where(Setting.org_id == org_id, Setting.key == key)
    )
    setting = result.first()
    if setting:
        setting.value = value
    else:
        setting = Setting(org_id=org_id, key=key, value=value)
        session.add(setting)
    await session.flush()
    return setting


# ------------------------------------------------------------ notifications
async def notify(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    type_: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    payload: dict | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id, type=type_, title=title, body=body, link=link, payload=payload or {}
    )
    session.add(notification)
    await session.flush()
    return notification


# ------------------------------------------------------------ single-org org
async def get_organization(session: AsyncSession) -> Organization:
    result = await session.scalars(select(Organization).limit(1))
    org = result.first()
    if not org:
        raise NotFoundError("Organization not found — run make seed")
    return org
