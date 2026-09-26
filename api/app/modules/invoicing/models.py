"""Invoicing module — AR/AP invoices, payments, allocations. Schema `invoicing`."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class Invoice(Base, TimestampMixin, UuidPk):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "invoicing"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # ar | ap | ar_credit | ap_credit
    invoice_type: Mapped[str] = mapped_column(String(12), index=True)
    original_invoice_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    applied_credits: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    number: Mapped[str | None] = mapped_column(String(30), unique=False)  # assigned at post
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.contacts.id", ondelete="SET NULL")
    )
    party_name: Mapped[str | None] = mapped_column(String(200))  # snapshot
    source_type: Mapped[str | None] = mapped_column(String(30))  # sales_order | purchase_order
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_number: Mapped[str | None] = mapped_column(String(30))
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | posted | partial | paid | void

    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["InvoiceLine"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="InvoiceLine.position"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = {"schema": "invoicing"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoicing.invoices.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.products.id", ondelete="SET NULL")
    )
    product_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    uom_code: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    tax_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.taxes.id", ondelete="SET NULL")
    )
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    line_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class Payment(Base, TimestampMixin, UuidPk):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "invoicing"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(10))  # in (customer) | out (supplier)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.contacts.id", ondelete="RESTRICT"))
    party_name: Mapped[str | None] = mapped_column(String(200))
    payment_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    method: Mapped[str] = mapped_column(String(20), default="bank")  # cash|bank|card|transfer|other
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="recorded", index=True)  # recorded|void
    credit_note_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column()

    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = {"schema": "invoicing"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoicing.payments.id", ondelete="CASCADE"), index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoicing.invoices.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
