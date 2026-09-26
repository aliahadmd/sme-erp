"""Purchasing module — purchase orders. Schema `purchasing`."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class PurchaseOrder(Base, TimestampMixin, UuidPk):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "purchasing"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.branches.id", ondelete="SET NULL")
    )
    number: Mapped[str] = mapped_column(String(30), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.contacts.id", ondelete="SET NULL")
    )
    supplier_name: Mapped[str | None] = mapped_column(String(200))  # snapshot
    order_date: Mapped[date] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | confirmed | received | invoiced | closed | cancelled

    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    confirmed_at: Mapped[date | None] = mapped_column(Date)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PurchaseOrderLine.position",
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = {"schema": "purchasing"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchasing.purchase_orders.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.products.id", ondelete="SET NULL")
    )
    product_name: Mapped[str | None] = mapped_column(String(200))  # snapshot
    description: Mapped[str | None] = mapped_column(Text)

    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    uom_code: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    tax_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.taxes.id", ondelete="SET NULL")
    )
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    qty_received: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    qty_invoiced: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)

    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    line_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
