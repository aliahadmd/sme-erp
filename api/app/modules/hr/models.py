"""HR module — departments, employees, leave. Schema `hr`."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class Department(Base, TimestampMixin, UuidPk):
    __tablename__ = "departments"
    __table_args__ = {"schema": "hr"}

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(100))
    manager_id: Mapped[uuid.UUID | None] = mapped_column()


class Employee(Base, TimestampMixin, UuidPk):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "hr"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(20), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    work_email: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL"), unique=True
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hr.departments.id", ondelete="SET NULL")
    )
    position: Mapped[str | None] = mapped_column(String(100))
    hired_at: Mapped[date | None] = mapped_column(Date)
    terminated_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")


class LeaveType(Base, TimestampMixin, UuidPk):
    __tablename__ = "leave_types"
    __table_args__ = (UniqueConstraint("org_id", "name"), {"schema": "hr"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(50))
    days_per_year: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=0)
    accrues: Mapped[bool] = mapped_column(default=False)


class LeaveRequest(Base, TimestampMixin, UuidPk):
    __tablename__ = "leave_requests"
    __table_args__ = {"schema": "hr"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hr.employees.id", ondelete="CASCADE"), index=True
    )
    type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hr.leave_types.id", ondelete="RESTRICT"))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    days: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    reason: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending | approved | rejected | cancelled
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[date | None] = mapped_column(Date)
