"""HR module API — employees, departments, leave requests."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.models import Permission, Role, RolePermission, User, UserRole
from app.modules.core.service import get_organization, notify, write_audit
from app.modules.hr.models import (
    Department,
    Employee,
    LeaveRequest,
    LeaveType,
)
from app.shared.numbering import next_number

router = APIRouter(prefix="/hr")


async def _employee_or_404(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> Employee:
    employee = await session.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")
    return employee


# ---------------------------------------------------------------- departments
class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    _user: CurrentUser = Depends(require("hr.employee.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Department]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(Department).where(Department.org_id == org.id).order_by(Department.name)
        )
    )


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    body: DepartmentIn,
    user: CurrentUser = Depends(require("hr.employee.create")),
    session: AsyncSession = Depends(get_session),
) -> Department:
    org = await get_organization(session)
    department = Department(org_id=org.id, name=body.name)
    session.add(department)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="hr.department",
        entity_id=department.id,
        after={"name": department.name},
    )
    await session.commit()
    await session.refresh(department)
    return department


# ------------------------------------------------------------------ employees
class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    work_email: str | None = None
    department_id: uuid.UUID | None = None
    position: str | None = None
    hired_at: date | None = None
    # Link to a login so the employee can request their own leave.
    user_id: uuid.UUID | None = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    full_name: str
    work_email: str | None
    department_id: uuid.UUID | None
    position: str | None
    hired_at: date | None
    status: str
    user_id: uuid.UUID | None = None


@router.get("/employees/me", response_model=EmployeeOut)
async def my_employee_record(
    user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> Employee:
    """The caller's own employee record (self-service leave)."""
    org = await get_organization(session)
    employee = (
        await session.scalars(
            select(Employee).where(Employee.org_id == org.id, Employee.user_id == user.id)
        )
    ).first()
    if employee is None:
        raise NotFoundError("No employee record is linked to your user")
    return employee


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(
    _user: CurrentUser = Depends(require("hr.employee.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Employee]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(Employee).where(Employee.org_id == org.id).order_by(Employee.full_name)
        )
    )


@router.post("/employees", response_model=EmployeeOut, status_code=201)
async def create_employee(
    body: EmployeeIn,
    user: CurrentUser = Depends(require("hr.employee.create")),
    session: AsyncSession = Depends(get_session),
) -> Employee:
    org = await get_organization(session)
    if body.user_id is not None:
        if await session.get(User, body.user_id) is None:
            raise ValidationError("Unknown user")
        linked = await session.scalar(
            select(Employee.id).where(Employee.org_id == org.id, Employee.user_id == body.user_id)
        )
        if linked:
            raise ConflictError("That user is already linked to an employee record")
    prefix = "EMP"
    number = await next_number(session, org.id, "employee", prefix)
    employee = Employee(
        org_id=org.id,
        number=number,
        full_name=body.full_name,
        work_email=body.work_email,
        department_id=body.department_id,
        position=body.position,
        hired_at=body.hired_at or date.today(),
        user_id=body.user_id,
    )
    session.add(employee)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="hr.employee",
        entity_id=employee.id,
        after={"number": number, "name": employee.full_name},
    )
    await session.commit()
    await session.refresh(employee)
    return employee


# ---------------------------------------------------------------------- leave
class LeaveTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    days_per_year: Decimal = Field(0, ge=0, le=365)
    accrues: bool = False


class LeaveTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    days_per_year: Decimal
    accrues: bool


@router.get("/leave-types", response_model=list[LeaveTypeOut])
async def list_leave_types(
    # Anyone who may request leave needs the types (self-service employees).
    _user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> list[LeaveType]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(LeaveType).where(LeaveType.org_id == org.id).order_by(LeaveType.name)
        )
    )


@router.post("/leave-types", response_model=LeaveTypeOut, status_code=201)
async def create_leave_type(
    body: LeaveTypeIn,
    # Leave policy is HR's to manage (the hr role has this permission).
    user: CurrentUser = Depends(require("hr.employee.update")),
    session: AsyncSession = Depends(get_session),
) -> LeaveType:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(LeaveType).where(LeaveType.org_id == org.id, LeaveType.name == body.name)
        )
    ).first()
    if existing:
        raise ConflictError(f"Leave type {body.name} already exists")
    leave_type = LeaveType(org_id=org.id, name=body.name, days_per_year=body.days_per_year)
    session.add(leave_type)
    await session.commit()
    await session.refresh(leave_type)
    return leave_type


# ---------------------------------------------------------------------- leave
class LeaveRequestIn(BaseModel):
    employee_id: uuid.UUID
    type_id: uuid.UUID
    date_from: date
    date_to: date
    reason: str | None = Field(None, max_length=300)


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    type_id: uuid.UUID
    date_from: date
    date_to: date
    days: Decimal
    reason: str | None
    status: str
    approver_id: uuid.UUID | None
    decided_at: date | None


async def _has_permission(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """Superusers pass everything; otherwise the user's roles must carry the code."""
    if (
        await session.scalars(
            select(User.id).where(User.id == user_id, User.is_superuser.is_(True))
        )
    ).first():
        return True
    return (
        await session.scalars(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(UserRole.user_id == user_id, Permission.code == code)
            .distinct()
        )
    ).first() is not None


@router.get("/leave-requests", response_model=list[LeaveRequestOut])
async def list_leave_requests(
    status: str | None = None,
    user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> list[LeaveRequest]:
    org = await get_organization(session)
    stmt = (
        select(LeaveRequest)
        .where(LeaveRequest.org_id == org.id)
        .order_by(LeaveRequest.created_at.desc())
    )
    if status:
        stmt = stmt.where(LeaveRequest.status == status)
    # Non-approvers only see requests for their own employee record.
    if not user.is_superuser and not await _has_permission(session, user.id, "hr.leave.approve"):
        own = (
            await session.scalars(
                select(Employee).where(Employee.org_id == org.id, Employee.user_id == user.id)
            )
        ).first()
        if own is None:
            return []
        stmt = stmt.where(LeaveRequest.employee_id == own.id)
    return list((await session.scalars(stmt)).all())


def working_days_by_year(date_from: date, date_to: date) -> dict[int, Decimal]:
    """Monday–Friday days in [date_from, date_to], split per calendar year
    (public holidays are not modelled yet)."""
    days: dict[int, Decimal] = {}
    current = date_from
    while current <= date_to:
        if current.weekday() < 5:
            days[current.year] = days.get(current.year, Decimal("0")) + 1
        current += timedelta(days=1)
    return days


async def _check_allowance(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type: LeaveType,
    date_from: date,
    date_to: date,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Annual allowance per calendar year, counting approved AND pending
    requests (pending ones are commitments too)."""
    wanted = working_days_by_year(date_from, date_to)
    stmt = select(LeaveRequest).where(
        LeaveRequest.org_id == org_id,
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.type_id == leave_type.id,
        LeaveRequest.status.in_(("pending", "approved")),
    )
    if exclude_id is not None:
        stmt = stmt.where(LeaveRequest.id != exclude_id)
    used: dict[int, Decimal] = {}
    for req in (await session.scalars(stmt)).all():
        for year, count in working_days_by_year(req.date_from, req.date_to).items():
            used[year] = used.get(year, Decimal("0")) + count
    for year, count in wanted.items():
        if used.get(year, Decimal("0")) + count > leave_type.days_per_year:
            raise ValidationError(
                f"Exceeds the annual allowance of {leave_type.days_per_year} days for "
                f"{leave_type.name} in {year} ({used.get(year, Decimal('0'))} already booked)"
            )


async def _approver_ids(session: AsyncSession) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(User.id)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(User.is_active.is_(True), Permission.code == "hr.leave.approve")
                .distinct()
            )
        ).all()
    )


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
async def create_leave_request(
    body: LeaveRequestIn,
    user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> LeaveRequest:
    org = await get_organization(session)
    employee = await _employee_or_404(session, org.id, body.employee_id)
    # Ownership: employees may only request for themselves; approvers for anyone.
    if not user.is_superuser and not await _has_permission(session, user.id, "hr.leave.approve"):
        own = (
            await session.scalars(
                select(Employee).where(Employee.org_id == org.id, Employee.user_id == user.id)
            )
        ).first()
        if own is None or own.id != employee.id:
            raise PermissionDeniedError(
                "You can only submit leave requests for your own employee record"
            )
    if body.date_to < body.date_from:
        raise ValidationError("date_to must be on or after date_from")
    days = sum(working_days_by_year(body.date_from, body.date_to).values(), Decimal("0"))
    if days == 0:
        raise ValidationError("The requested period contains no working days")

    # Overlap guard: no pending/approved request may overlap the new one
    existing = (
        await session.scalars(
            select(LeaveRequest).where(
                LeaveRequest.org_id == org.id,
                LeaveRequest.employee_id == employee.id,
                LeaveRequest.status.in_(("pending", "approved")),
            )
        )
    ).all()
    for req in existing:
        if not (body.date_to < req.date_from or body.date_from > req.date_to):
            raise ConflictError(
                f"Overlaps an existing {req.status} request ({req.date_from} … {req.date_to})"
            )

    leave_type = await session.get(LeaveType, body.type_id)
    if not leave_type or leave_type.org_id != org.id:
        raise NotFoundError("Leave type not found")
    await _check_allowance(session, org.id, employee.id, leave_type, body.date_from, body.date_to)

    request = LeaveRequest(
        org_id=org.id,
        employee_id=employee.id,
        type_id=body.type_id,
        date_from=body.date_from,
        date_to=body.date_to,
        days=days,
        reason=body.reason,
    )
    session.add(request)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="hr.leave_request",
        entity_id=request.id,
        after={"employee": employee.full_name, "days": str(days)},
    )
    for approver_id in await _approver_ids(session):
        await notify(
            session,
            user_id=approver_id,
            type_="leave_request",
            title=f"Leave request: {employee.full_name}",
            body=f"{days} working days from {body.date_from}",
            link="/hr/leave",
        )
    await session.commit()
    await session.refresh(request)
    return request


async def _pending_request(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> LeaveRequest:
    request = (
        await session.scalars(
            select(LeaveRequest)
            .where(LeaveRequest.id == request_id, LeaveRequest.org_id == org_id)
            .with_for_update(of=LeaveRequest)
        )
    ).first()
    if not request:
        raise NotFoundError("Leave request not found")
    if request.status != "pending":
        raise ConflictError(f"Request is already {request.status}")
    return request


async def _decide(
    request_id: uuid.UUID,
    decision: str,
    user: CurrentUser,
    session: AsyncSession,
) -> LeaveRequest:
    org = await get_organization(session)
    request = await _pending_request(session, org.id, request_id)
    if request.employee.user_id is not None and request.employee.user_id == user.id:
        raise PermissionDeniedError("You cannot decide on your own leave request")
    if decision == "approved":
        leave_type = await session.get(LeaveType, request.type_id)
        # Re-check at approval: other requests may have been approved since.
        await _check_allowance(
            session,
            org.id,
            request.employee_id,
            leave_type,
            request.date_from,
            request.date_to,
            exclude_id=request.id,
        )
    request.status = decision
    request.approver_id = user.id
    request.decided_at = date.today()
    await write_audit(
        session,
        actor=user.user,
        action="approve" if decision == "approved" else "reject",
        entity_type="hr.leave_request",
        entity_id=request.id,
        after={"status": decision},
    )
    if request.employee.user_id:
        await notify(
            session,
            user_id=request.employee.user_id,
            type_=f"leave_{decision}",
            title=f"Leave {decision} ({request.days} days)",
            payload={"request_id": str(request.id)},
            link="/hr/leave",
        )
    await session.commit()
    await session.refresh(request)
    return request


@router.post("/leave-requests/{request_id}/approve", response_model=LeaveRequestOut)
async def approve_leave_request(
    request_id: uuid.UUID,
    user: CurrentUser = Depends(require("hr.leave.approve")),
    session: AsyncSession = Depends(get_session),
) -> LeaveRequest:
    return await _decide(request_id, "approved", user, session)


@router.post("/leave-requests/{request_id}/reject", response_model=LeaveRequestOut)
async def reject_leave_request(
    request_id: uuid.UUID,
    user: CurrentUser = Depends(require("hr.leave.approve")),
    session: AsyncSession = Depends(get_session),
) -> LeaveRequest:
    return await _decide(request_id, "rejected", user, session)


@router.post("/leave-requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_leave_request(
    request_id: uuid.UUID,
    user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> LeaveRequest:
    """The requester (or an approver) withdraws a pending request."""
    org = await get_organization(session)
    request = await _pending_request(session, org.id, request_id)
    is_owner = request.employee.user_id == user.id
    if not is_owner and not await _has_permission(session, user.id, "hr.leave.approve"):
        raise PermissionDeniedError("Only the requester or an approver can cancel a request")
    request.status = "cancelled"
    request.decided_at = date.today()
    await write_audit(
        session,
        actor=user.user,
        action="cancel",
        entity_type="hr.leave_request",
        entity_id=request.id,
        after={"status": "cancelled"},
    )
    await session.commit()
    await session.refresh(request)
    return request
