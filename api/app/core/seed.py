"""Bootstrap seed — idempotent: safe to run on every deploy via `make seed`.

Creates (if missing): the organization, a MAIN branch, the permission catalog,
the six system roles with their permission mappings, the admin user from env,
and default settings.
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.modules.core.models import (
    Branch,
    Organization,
    Permission,
    Role,
    RolePermission,
    Setting,
    User,
    UserRole,
)
from app.modules.core.permissions import PERMISSION_CATALOG, SYSTEM_ROLES, system_role_permissions
from app.modules.core.service import DEFAULT_SETTINGS, create_user

logger = get_logger(__name__)


async def seed(session) -> None:  # noqa: ANN001
    settings = get_settings()

    # Organization (single-org deployment in phase 1)
    org = (await session.scalars(select(Organization).limit(1))).first()
    if not org:
        org = Organization(name="My Company", base_currency="USD")
        session.add(org)
        await session.flush()
        logger.info("seed_org_created", name=org.name)

    # Main branch
    branch = (await session.scalars(select(Branch).where(Branch.code == "MAIN"))).first()
    if not branch:
        branch = Branch(org_id=org.id, code="MAIN", name="Main")
        session.add(branch)
        await session.flush()
        logger.info("seed_branch_created", code="MAIN")

    # Permission catalog
    existing = set((await session.scalars(select(Permission.code))).all())
    for code, module, action, description in PERMISSION_CATALOG:
        if code not in existing:
            session.add(
                Permission(code=code, module=module, action=action, description=description)
            )
    await session.flush()
    permissions_by_code = {p.code: p for p in (await session.scalars(select(Permission))).all()}

    # System roles
    for role_code, spec in SYSTEM_ROLES.items():
        role = (await session.scalars(select(Role).where(Role.code == role_code))).first()
        created = False
        if not role:
            role = Role(
                code=role_code, name=spec["name"], description=spec["description"], is_system=True
            )
            session.add(role)
            await session.flush()
            created = True
        if created:
            current: set[str] = set()
        else:
            current = set(
                (
                    await session.scalars(
                        select(Permission.code)
                        .join(RolePermission, RolePermission.permission_id == Permission.id)
                        .where(RolePermission.role_id == role.id)
                    )
                ).all()
            )
        wanted = set(system_role_permissions(role_code))
        for perm_code in wanted - current:
            session.add(
                RolePermission(role_id=role.id, permission_id=permissions_by_code[perm_code].id)
            )

    # Admin user
    admin = (
        await session.scalars(select(User).where(User.email == settings.admin_email.lower()))
    ).first()
    if not admin:
        admin_role = (await session.scalars(select(Role).where(Role.code == "admin"))).first()
        admin = await create_user(
            session,
            email=settings.admin_email,
            password=settings.admin_password,
            full_name=settings.admin_full_name,
            is_superuser=True,
            default_branch_id=branch.id,
        )
        if admin_role:
            session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        logger.info("seed_admin_created", email=settings.admin_email)

    # Default settings
    for key, value in DEFAULT_SETTINGS.items():
        existing_setting = (
            await session.scalars(
                select(Setting).where(Setting.org_id == org.id, Setting.key == key)
            )
        ).first()
        if not existing_setting:
            session.add(Setting(org_id=org.id, key=key, value=value))

    await session.commit()
    total = await session.scalar(select(func.count()).select_from(Permission))
    logger.info("seed_done", permissions=total)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.environment)
    engine = create_async_engine(settings.database_url)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionFactory() as session:
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
