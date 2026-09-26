"""HR module API — employees, departments, leave requests."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
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
    _user: CurrentUser = Depends(require("hr.employee.read")),
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
    user: CurrentUser = Depends(require("core.settings.update")),
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


@router.get("/leave-requests", response_model=list[LeaveRequestOut])
async def list_leave_requests(
    status: str | None = None,
    _user: CurrentUser = Depends(require("hr.leave.request")),
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
    return list((await session.scalars(stmt)).all())


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
async def create_leave_request(
    body: LeaveRequestIn,
    user: CurrentUser = Depends(require("hr.leave.request")),
    session: AsyncSession = Depends(get_session),
) -> LeaveRequest:
    org = await get_organization(session)
    employee = await _employee_or_404(session, org.id, body.employee_id)
    if body.date_to < body.date_from:
        raise ValidationError("date_to must be on or after date_from")
    days = Decimal(str((body.date_to - body.date_from).days + 1))

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
    if not leave_type:
        raise NotFoundError("Leave type not found")
    used = sum(
        (r.days for r in existing if r.type_id == body.type_id and r.status == "approved"),
        Decimal("0"),
    )
    if used + days > leave_type.days_per_year:
        raise ValidationError(
            f"Exceeds the annual allowance of {leave_type.days_per_year} days for {leave_type.name}"
        )

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
    # Notify approvers (users holding hr.leave.approve)

    from app.modules.core.models import Permission, Role, RolePermission, User, UserRole

    approver_ids = (
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
    for approver_id in approver_ids:
        await notify(
            session,
            user_id=approver_id,
            type_="leave_request",
            title=f"Leave request: {employee.full_name}",
            body=f"{days} days from {body.date_from}",
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
    org = await get_organization(session)
    request = (
        await session.scalars(
            select(LeaveRequest).where(LeaveRequest.id == request_id, LeaveRequest.org_id == org.id)
        )
    ).first()
    if not request:
        raise NotFoundError("Leave request not found")
    if request.status != "pending":
        raise ConflictError(f"Request is already {request.status}")
    request.status = "approved"
    request.approver_id = user.id
    request.decided_at = date.today()
    await write_audit(
        session,
        actor=user.user,
        action="approve",
        entity_type="hr.leave_request",
        entity_id=request.id,
        after={"status": "approved"},
    )
    await notify(
        session,
        user_id=request.employee.user_id or user.id,
        type_="leave_approved",
        title=f"Leave approved ({request.days} days)",
        payload={"request_id": str(request.id)},
    )
    await session.commit()
    await session.refresh(request)
    return request
