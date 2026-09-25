"""Pydantic schemas for the core module API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.shared.pagination import Page

# Page[...] instances used as response models
UserPage = Page  # re-export for clarity in routers


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    legal_name: str | None
    tax_id: str | None
    base_currency: str
    email: str | None
    phone: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    country: str | None


class OrganizationUpdateIn(BaseModel):
    name: str | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    base_currency: str | None = Field(None, min_length=3, max_length=3)
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    country: str | None = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    address: str | None
    is_active: bool


class BranchIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    is_active: bool = True


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permission_codes: list[str] = []


class RoleIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    permission_codes: list[str] = []


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    default_branch_id: uuid.UUID | None
    last_login_at: datetime | None
    role_codes: list[str] = []


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=200)
    is_superuser: bool = False
    role_codes: list[str] = []
    default_branch_id: uuid.UUID | None = None


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    is_active: bool | None = None
    password: str | None = Field(None, min_length=8)
    role_codes: list[str] | None = None
    default_branch_id: uuid.UUID | None = None


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_superuser: bool
    roles: list[str]
    permissions: list[str]


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RefreshedTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: dict


class SettingIn(BaseModel):
    value: dict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict | None
    after: dict | None
    ip: str | None
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    title: str
    body: str | None
    link: str | None
    payload: dict
    read_at: datetime | None
    created_at: datetime
