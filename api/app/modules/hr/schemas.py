"""Portable HR API contracts shared by the server and Workers."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class EmployeeIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    work_email: str | None = Field(None, max_length=255)
    department_id: uuid.UUID | None = None
    position: str | None = Field(None, max_length=100)
    hired_at: date | None = None
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


class LeaveTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    days_per_year: Decimal = Field(Decimal("0"), ge=0, le=365)
    accrues: bool = False


class LeaveTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    days_per_year: Decimal
    accrues: bool


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
