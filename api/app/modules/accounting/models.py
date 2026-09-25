"""Accounting module — chart of accounts, journal entries. Schema `accounting`."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class Account(Base, TimestampMixin, UuidPk):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "accounting"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))  # asset|liability|equity|income|expense
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounting.accounts.id", ondelete="SET NULL")
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JournalEntry(Base, TimestampMixin, UuidPk):
    __tablename__ = "journal_entries"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "accounting"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(30))
    entry_date: Mapped[date] = mapped_column(Date)
    memo: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    # ar_invoice | ap_invoice | payment | delivery | adjustment | manual
    source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="posted")
    created_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["JournalLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = {"schema": "accounting"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounting.journal_entries.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounting.accounts.id", ondelete="RESTRICT"), index=True
    )
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    account: Mapped[Account] = relationship(lazy="joined")
    entry: Mapped[JournalEntry] = relationship(lazy="joined")
