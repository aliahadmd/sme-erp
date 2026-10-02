"""Dev-only test accounts — one user per system role, plus edge cases.

Usage: `make seed-users` (after `make seed`). Idempotent: existing users are
left untouched. Refuses to run unless ENVIRONMENT=dev or ALLOW_TEST_USERS=true
(demo deployments — set TEST_USER_PASSWORD there; never on real production).

Accounts (all use TEST_USER_PASSWORD, default below):

| email                     | role(s)     | what to test                                  |
|---------------------------|-------------|-----------------------------------------------|
| accountant@example.com    | accountant  | invoices, payments, journal, trial balance    |
| sales@example.com         | sales       | customers, quotes, sales orders, AR invoices  |
| purchasing@example.com    | purchasing  | suppliers, purchase orders, receipts          |
| warehouse@example.com     | warehouse   | stock, receipts, deliveries, adjustments      |
| hr@example.com            | hr          | employees, approve/reject leave               |
| viewer@example.com        | viewer      | read-only everywhere (writes must be 403)     |
| employee@example.com      | employee    | own leave requests only (custom role)         |
| disabled@example.com      | viewer      | INACTIVE — login must be rejected             |

The superuser comes from ADMIN_EMAIL / ADMIN_PASSWORD in .env (`make seed`).
"""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.modules.core.models import Permission, Role, User
from app.modules.core.service import create_user, get_organization, get_user_by_email

logger = get_logger(__name__)

TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "Test-pass-123")

# (email, full name, role codes, active)
TEST_USERS: list[tuple[str, str, list[str], bool]] = [
    ("accountant@example.com", "Avery Accountant", ["accountant"], True),
    ("sales@example.com", "Sam Sales", ["sales"], True),
    ("purchasing@example.com", "Parker Purchasing", ["purchasing"], True),
    ("warehouse@example.com", "Wren Warehouse", ["warehouse"], True),
    ("hr@example.com", "Harper HR", ["hr"], True),
    ("viewer@example.com", "Vic Viewer", ["viewer"], True),
    ("employee@example.com", "Emery Employee", ["employee"], True),
    ("disabled@example.com", "Dana Disabled", ["viewer"], False),
]

# Self-service role: an employee can only file and see their own leave.
EMPLOYEE_ROLE = {
    "code": "employee",
    "name": "Employee",
    "description": "Self-service: own leave requests only.",
    "permissions": ["hr.leave.request"],
}


async def _ensure_employee_role(session) -> None:  # noqa: ANN001
    role = (await session.scalars(select(Role).where(Role.code == EMPLOYEE_ROLE["code"]))).first()
    if role:
        return
    permissions = list(
        await session.scalars(
            select(Permission).where(Permission.code.in_(EMPLOYEE_ROLE["permissions"]))
        )
    )
    role = Role(
        code=EMPLOYEE_ROLE["code"],
        name=EMPLOYEE_ROLE["name"],
        description=EMPLOYEE_ROLE["description"],
        is_system=False,
    )
    role.permissions = permissions
    session.add(role)
    await session.flush()


async def _link_employee_record(session, user: User) -> None:  # noqa: ANN001
    """The employee user needs an HR record to request leave for."""
    from app.modules.hr.models import Employee
    from app.shared.numbering import next_number

    org = await get_organization(session)
    existing = (await session.scalars(select(Employee).where(Employee.user_id == user.id))).first()
    if existing:
        return
    session.add(
        Employee(
            org_id=org.id,
            number=await next_number(session, org.id, "employee", "EMP"),
            full_name=user.full_name,
            work_email=user.email,
            user_id=user.id,
            position="Staff",
        )
    )
    await session.flush()


# Leave types so self-service leave can be tried immediately.
LEAVE_TYPES = [("Annual leave", "20"), ("Sick leave", "10")]


async def _ensure_leave_types(session) -> None:  # noqa: ANN001
    from decimal import Decimal

    from app.modules.hr.models import LeaveType

    org = await get_organization(session)
    existing = set(await session.scalars(select(LeaveType.name).where(LeaveType.org_id == org.id)))
    for name, days in LEAVE_TYPES:
        if name not in existing:
            session.add(LeaveType(org_id=org.id, name=name, days_per_year=Decimal(days)))
    await session.flush()


async def seed_test_users(session) -> list[str]:  # noqa: ANN001
    await _ensure_employee_role(session)
    await _ensure_leave_types(session)
    created: list[str] = []
    for email, full_name, role_codes, active in TEST_USERS:
        user = await get_user_by_email(session, email)
        if user is None:
            roles = list(await session.scalars(select(Role).where(Role.code.in_(role_codes))))
            user = await create_user(
                session,
                email=email,
                password=TEST_USER_PASSWORD,
                full_name=full_name,
                role_ids=[r.id for r in roles],
            )
            user.is_active = active
            created.append(email)
        if "employee" in role_codes:
            await _link_employee_record(session, user)
    await session.commit()
    return created


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.environment)
    if settings.environment != "dev" and not settings.allow_test_users:
        raise SystemExit("seed_test_users only runs with ENVIRONMENT=dev or ALLOW_TEST_USERS=true")
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        created = await seed_test_users(session)
    await engine.dispose()
    logger.info("test_users_seeded", created=created, total=len(TEST_USERS))


if __name__ == "__main__":
    asyncio.run(main())
