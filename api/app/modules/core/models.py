"""ERP Core models — schema `core`.

Every business module builds on these: organization, branches, users,
roles & permissions, settings, audit log, notifications, document numbering.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class Organization(Base, TimestampMixin, UuidPk):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "core"}

    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(50))
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(2))

    branches: Mapped[list["Branch"]] = relationship(back_populates="organization")


class Branch(Base, TimestampMixin, UuidPk):
    __tablename__ = "branches"
    __table_args__ = {"schema": "core"}

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.organizations.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    organization: Mapped[Organization] = relationship(back_populates="branches")


class User(Base, TimestampMixin, UuidPk):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    default_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.branches.id", ondelete="SET NULL")
    )
    email_prefs: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list["Role"]] = relationship(secondary="core.user_roles", lazy="selectin")


class Role(Base, TimestampMixin, UuidPk):
    __tablename__ = "roles"
    __table_args__ = {"schema": "core"}

    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list["Permission"]] = relationship(
        secondary="core.role_permissions", lazy="selectin"
    )


class Permission(Base, UuidPk):
    __tablename__ = "permissions"
    __table_args__ = {"schema": "core"}

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    module: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.roles.id", ondelete="CASCADE"), primary_key=True
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = {"schema": "core"}

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.permissions.id", ondelete="CASCADE"), primary_key=True
    )


class Setting(Base, TimestampMixin, UuidPk):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("org_id", "key"), {"schema": "core"})

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.organizations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class AuditLog(Base):
    """Append-only. org_id/user_id are plain UUIDs (no FK) so history survives
    party/user deletion."""

    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    org_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", index=True
    )


class Notification(Base, UuidPk):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class DocumentSequence(Base):
    """Per org + entity + year numbering with gap-tolerant sequences."""

    __tablename__ = "document_sequences"
    __table_args__ = (UniqueConstraint("org_id", "entity", "year"), {"schema": "core"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    entity: Mapped[str] = mapped_column(String(50))
    year: Mapped[int] = mapped_column(Integer)
    last_number: Mapped[int] = mapped_column(Integer, default=0)
