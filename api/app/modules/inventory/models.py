"""Inventory module — warehouses, stock moves, receipts/deliveries/adjustments.

Every quantity change is an immutable `stock_moves` row; `stock` holds the
derived current state per (product, warehouse) and is updated in the same
transaction as its moves. Costing = moving average.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk, generate_uuid7


class Warehouse(Base, TimestampMixin, UuidPk):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "inventory"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.branches.id", ondelete="SET NULL")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class StockMove(Base):
    """Append-only. qty is signed (+in / −out). Corrections are reversing moves."""

    __tablename__ = "stock_moves"
    __table_args__ = {"schema": "inventory"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    move_type: Mapped[str] = mapped_column(String(20), index=True)
    # receipt | delivery | adjustment | transfer_in | transfer_out | reversal
    ref_type: Mapped[str | None] = mapped_column(String(50))
    ref_id: Mapped[uuid.UUID | None] = mapped_column()
    ref_number: Mapped[str | None] = mapped_column(String(30))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    cogs: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # outbound moves
    reason: Mapped[str | None] = mapped_column(Text)
    moved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    reverses_move_id: Mapped[uuid.UUID | None] = mapped_column(index=True)


class Stock(Base, TimestampMixin):
    """Current state per (product, warehouse) — maintained transactionally."""

    __tablename__ = "stock"
    __table_args__ = (
        UniqueConstraint("org_id", "product_id", "warehouse_id"),
        {"schema": "inventory"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(index=True)
    qty_on_hand: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    last_move_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _LineBase:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid7)
    product_id: Mapped[uuid.UUID] = mapped_column(index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)


class Receipt(Base, TimestampMixin, UuidPk):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "inventory"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(30))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.warehouses.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str | None] = mapped_column(String(30))  # purchase_order
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_number: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(20), default="draft", index=True
    )  # draft|posted|void
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["ReceiptLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class ReceiptLine(_LineBase, Base):
    __tablename__ = "receipt_lines"
    __table_args__ = {"schema": "inventory"}

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.receipts.id", ondelete="CASCADE"), index=True
    )


class Delivery(Base, TimestampMixin, UuidPk):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "inventory"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(30))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.warehouses.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str | None] = mapped_column(String(30))  # sales_order
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_number: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["DeliveryLine"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class DeliveryLine(_LineBase, Base):
    __tablename__ = "delivery_lines"
    __table_args__ = {"schema": "inventory"}

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.deliveries.id", ondelete="CASCADE"), index=True
    )


class Adjustment(Base, TimestampMixin, UuidPk):
    __tablename__ = "adjustments"
    __table_args__ = (UniqueConstraint("org_id", "number"), {"schema": "inventory"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(30))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.warehouses.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(30))  # stock_correction|damage|count|other
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column()

    lines: Mapped[list["AdjustmentLine"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class AdjustmentLine(_LineBase, Base):
    """qty may be negative (shrinkage) or positive (found stock)."""

    __tablename__ = "adjustment_lines"
    __table_args__ = {"schema": "inventory"}

    adjustment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.adjustments.id", ondelete="CASCADE"), index=True
    )
