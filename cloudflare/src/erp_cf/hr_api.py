"""HR read scopes and API contracts; mutations use the organization coordinator."""

import uuid
from typing import Annotated

from app.modules.hr import schemas
from fastapi import APIRouter, Depends, Request

from erp_cf.command_bus import invoke
from erp_cf.core_api import repository
from erp_cf.errors import NotFoundError
from erp_cf.hr_commands import EMPLOYEES, REQUESTS, TYPES, approver
from erp_cf.identity import Actor, current_actor, require

router = APIRouter(prefix="/api/hr", dependencies=[Depends(current_actor)])
LeaveActor = Annotated[Actor, Depends(require("hr.leave.request"))]


@router.get(
    "/departments",
    response_model=list[schemas.DepartmentOut],
    dependencies=[Depends(require("hr.employee.read"))],
)
async def departments(request: Request):
    return await repository(request, "hr_departments").find(order_by="name,id")


@router.post("/departments", response_model=schemas.DepartmentOut, status_code=201)
async def create_department(body: schemas.DepartmentIn, request: Request):
    return await invoke(request, "hr.department.create", {"body": body})


@router.get("/employees/me", response_model=schemas.EmployeeOut)
async def my_employee(request: Request, actor: LeaveActor):
    row = await repository(request, EMPLOYEES).first("user_id=?", (actor.id,))
    if not row:
        raise NotFoundError("No employee record is linked to your user")
    return row


@router.get(
    "/employees",
    response_model=list[schemas.EmployeeOut],
    dependencies=[Depends(require("hr.employee.read"))],
)
async def employees(request: Request):
    return await repository(request, EMPLOYEES).find(order_by="full_name,id")


@router.post("/employees", response_model=schemas.EmployeeOut, status_code=201)
async def create_employee(body: schemas.EmployeeIn, request: Request):
    return await invoke(request, "hr.employee.create", {"body": body})


@router.get(
    "/leave-types",
    response_model=list[schemas.LeaveTypeOut],
    dependencies=[Depends(require("hr.leave.request"))],
)
async def leave_types(request: Request):
    return await repository(request, TYPES).find(order_by="name,id")


@router.post("/leave-types", response_model=schemas.LeaveTypeOut, status_code=201)
async def create_leave_type(body: schemas.LeaveTypeIn, request: Request):
    return await invoke(request, "hr.leave_type.create", {"body": body})


@router.get("/leave-requests", response_model=list[schemas.LeaveRequestOut])
async def leave_requests(request: Request, actor: LeaveActor, status: str | None = None):
    clauses, params = ["1"], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if not approver(actor):
        own = await repository(request, EMPLOYEES).first("user_id=?", (actor.id,))
        if not own:
            return []
        clauses.append("employee_id=?")
        params.append(own["id"])
    return await repository(request, REQUESTS).find(
        " AND ".join(clauses), tuple(params), order_by="created_at DESC,id DESC"
    )


@router.post("/leave-requests", response_model=schemas.LeaveRequestOut, status_code=201)
async def create_leave(body: schemas.LeaveRequestIn, request: Request):
    return await invoke(request, "hr.leave.create", {"body": body})


def action_handler(action):
    async def handler(identity: uuid.UUID, request: Request):
        return await invoke(request, "hr.leave." + action, {"id": identity})

    return handler


for action in ["approve", "reject", "cancel"]:
    router.add_api_route(
        "/leave-requests/{identity}/" + action,
        action_handler(action),
        methods=["POST"],
        response_model=schemas.LeaveRequestOut,
    )
