"""CRM module — unified contacts (customers and/or suppliers). Schema `crm`."""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TimestampMixin, UuidPk


class Contact(Base, TimestampMixin, UuidPk):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "crm"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="company")  # company|person
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(50))

    is_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)

    emails: Mapped[list] = mapped_column(JSONB, default=list)  # [{label, value}]
    phones: Mapped[list] = mapped_column(JSONB, default=list)
    addresses: Mapped[list] = mapped_column(
        JSONB, default=list
    )  # [{label, line1, city, country, ...}]

    currency: Mapped[str] = mapped_column(String(3), default="USD")
    payment_terms_days: Mapped[int] = mapped_column(default=30)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    tags: Mapped[list] = mapped_column(JSONB, default=list)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|archived
