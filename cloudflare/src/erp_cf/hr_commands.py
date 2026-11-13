"""Atomic employee/leave commands with per-employee revision serialization."""

from decimal import Decimal

from app.modules.hr import schemas
from app.shared.working_days import working_days_by_year

from erp_cf.documents import today
from erp_cf.errors import ConflictError, PermissionDeniedError, ValidationError
from erp_cf.numbering import next_number
from erp_cf.store import decimal_values

EMPLOYEES = "hr_employees"
REQUESTS = "hr_leave_requests"
TYPES = "hr_leave_types"


def approver(actor):
    return actor.is_superuser or "hr.leave.approve" in actor.permissions


def notify(plan, user_id, type_, title, *, body=None, request_id=None):
    return plan.add(
        "core_notifications",
        {
            "user_id": user_id,
            "type": type_,
            "title": title[:200],
            "body": body,
            "link": "/hr/leave",
            "payload": {"request_id": request_id} if request_id else {},
        },
    )


async def department_create(plan, actor, payload):
    body = schemas.DepartmentIn.model_validate(payload["body"])
    row = plan.add("hr_departments", body.model_dump())
    plan.audit("create", "hr.department", row["id"], after={"name": row["name"]})
    return schemas.DepartmentOut.model_validate(row)


async def employee_create(plan, actor, payload):
    body = schemas.EmployeeIn.model_validate(payload["body"])
    if body.department_id:
        department = await plan.repository("hr_departments").get(str(body.department_id))
        plan.guard("hr_departments", department)
    if body.user_id:
        user = await plan.repository("core_users").get(str(body.user_id), required=False)
        if not user:
            raise ValidationError("Unknown user")
        plan.guard("core_users", user)
        if await plan.repository(EMPLOYEES).first("user_id=?", (str(body.user_id),)):
            raise ConflictError("That user is already linked to an employee record")
    row = plan.add(
        EMPLOYEES,
        body.model_dump()
        | {
            "number": await next_number(plan, "employee", "EMP"),
            "hired_at": body.hired_at or today(),
        },
    )
    plan.audit(
        "create",
        "hr.employee",
        row["id"],
        after={"number": row["number"], "name": row["full_name"]},
    )
    return schemas.EmployeeOut.model_validate(row)


async def type_create(plan, actor, payload):
    body = schemas.LeaveTypeIn.model_validate(payload["body"])
    if await plan.repository(TYPES).first("name=?", (body.name,)):
        raise ConflictError(f"Leave type {body.name} already exists")
    row = plan.add(TYPES, decimal_values(body.model_dump(), TYPES))
    plan.audit(
        "create",
        "hr.leave_type",
        row["id"],
        after={"name": row["name"], "days_per_year": str(row["days_per_year"])},
    )
    return schemas.LeaveTypeOut.model_validate(row)


class Leave:
    def __init__(self, plan, actor):
        self.plan, self.actor = plan, actor

    async def employee(self, identity):
        row = await self.plan.repository(EMPLOYEES).get(str(identity))
        # New requests change the employee revision even without changing a
        # business field. This guards the absence of overlap/allowance phantoms.
        return self.plan.update(EMPLOYEES, row, {})

    async def allowance(self, employee, leave_type, first, last, *, exclude=None):
        rows = await self.plan.repository(REQUESTS).find(
            "employee_id=? AND type_id=? AND status IN ('pending','approved')",
            (employee["id"], leave_type["id"]),
        )
        used = {}
        for row in rows:
            if row["id"] != exclude:
                for year, count in working_days_by_year(row["date_from"], row["date_to"]).items():
                    used[year] = used.get(year, Decimal(0)) + count
        for year, count in working_days_by_year(first, last).items():
            if used.get(year, Decimal(0)) + count > leave_type["days_per_year"]:
                raise ValidationError(
                    f"Exceeds the annual allowance of {leave_type['days_per_year']} days for "
                    f"{leave_type['name']} in {year} ({used.get(year, Decimal(0))} already booked)"
                )

    async def create(self, payload):
        body = schemas.LeaveRequestIn.model_validate(payload["body"])
        employee = await self.employee(body.employee_id)
        if not approver(self.actor) and employee["user_id"] != self.actor.id:
            raise PermissionDeniedError(
                "You can only submit leave requests for your own employee record"
            )
        if body.date_to < body.date_from:
            raise ValidationError("date_to must be on or after date_from")
        days = sum(working_days_by_year(body.date_from, body.date_to).values(), Decimal(0))
        if not days:
            raise ValidationError("The requested period contains no working days")
        rows = await self.plan.repository(REQUESTS).find(
            "employee_id=? AND status IN ('pending','approved') AND date_from<=? AND date_to>=?",
            (employee["id"], body.date_to.isoformat(), body.date_from.isoformat()),
        )
        if rows:
            row = rows[0]
            raise ConflictError(
                f"Overlaps an existing {row['status']} request "
                f"({row['date_from']} … {row['date_to']})"
            )
        leave_type = await self.plan.repository(TYPES).get(str(body.type_id))
        self.plan.guard(TYPES, leave_type)
        await self.allowance(employee, leave_type, body.date_from, body.date_to)
        row = self.plan.add(REQUESTS, decimal_values(body.model_dump() | {"days": days}, REQUESTS))
        self.plan.audit(
            "create",
            "hr.leave_request",
            row["id"],
            after={"employee": employee["full_name"], "days": str(row["days"])},
        )
        recipients = await self.plan.db.all(
            "SELECT u.id FROM core_users u WHERE u.is_active=1 AND (u.is_superuser=1 OR EXISTS("
            "SELECT 1 FROM core_user_roles ur "
            "JOIN core_role_permissions rp ON rp.role_id=ur.role_id "
            "JOIN core_permissions p ON p.id=rp.permission_id WHERE ur.user_id=u.id AND p.code=?))",
            ("hr.leave.approve",),
        )
        for recipient in recipients:
            notify(
                self.plan,
                recipient["id"],
                "leave_request",
                f"Leave request: {employee['full_name']}",
                body=f"{days} working days from {body.date_from}",
                request_id=row["id"],
            )
        return schemas.LeaveRequestOut.model_validate(row)

    async def decide(self, payload, decision):
        row = await self.plan.repository(REQUESTS).get(payload["id"])
        if row["status"] != "pending":
            raise ConflictError(f"Request is already {row['status']}")
        employee = await self.employee(row["employee_id"])
        owner = employee["user_id"] == self.actor.id
        if decision == "cancelled":
            if not owner and not approver(self.actor):
                raise PermissionDeniedError(
                    "Only the requester or an approver can cancel a request"
                )
        elif owner:
            raise PermissionDeniedError("You cannot decide on your own leave request")
        if decision == "approved":
            leave_type = await self.plan.repository(TYPES).get(row["type_id"])
            self.plan.guard(TYPES, leave_type)
            await self.allowance(
                employee, leave_type, row["date_from"], row["date_to"], exclude=row["id"]
            )
        updated = self.plan.update(
            REQUESTS,
            row,
            {
                "status": decision,
                "decided_at": today(),
                **({"approver_id": self.actor.id} if decision != "cancelled" else {}),
            },
        )
        action = {"approved": "approve", "rejected": "reject", "cancelled": "cancel"}[decision]
        self.plan.audit(action, "hr.leave_request", row["id"], after={"status": decision})
        if employee["user_id"] and decision != "cancelled":
            notify(
                self.plan,
                employee["user_id"],
                f"leave_{decision}",
                f"Leave {decision} ({row['days']} days)",
                request_id=row["id"],
            )
        return schemas.LeaveRequestOut.model_validate(updated)


async def leave_create(plan, actor, payload):
    return await Leave(plan, actor).create(payload)


def decision_handler(decision):
    async def handler(plan, actor, payload):
        return await Leave(plan, actor).decide(payload, decision)

    return handler


COMMANDS = {
    "hr.department.create": ("hr.employee.create", 201, department_create),
    "hr.employee.create": ("hr.employee.create", 201, employee_create),
    "hr.leave_type.create": ("hr.employee.update", 201, type_create),
    "hr.leave.create": ("hr.leave.request", 201, leave_create),
    "hr.leave.approve": ("hr.leave.approve", 200, decision_handler("approved")),
    "hr.leave.reject": ("hr.leave.approve", 200, decision_handler("rejected")),
    "hr.leave.cancel": ("hr.leave.request", 200, decision_handler("cancelled")),
}
