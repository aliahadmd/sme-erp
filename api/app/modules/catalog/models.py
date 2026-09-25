"""Catalog module — products, categories, UoM, taxes. Schema `catalog`."""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TimestampMixin, UuidPk


class Uom(Base, TimestampMixin, UuidPk):
    __tablename__ = "uoms"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "catalog"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))


class Tax(Base, TimestampMixin, UuidPk):
    __tablename__ = "taxes"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "catalog"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    applies_to: Mapped[str] = mapped_column(String(10), default="both")  # sale|purchase|both
    is_default_sale: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default_purchase: Mapped[bool] = mapped_column(Boolean, default=False)


class ProductCategory(Base, TimestampMixin, UuidPk):
    __tablename__ = "product_categories"
    __table_args__ = {"schema": "catalog"}

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.product_categories.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(100))


class Product(Base, TimestampMixin, UuidPk):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("org_id", "sku"), {"schema": "catalog"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    sku: Mapped[str] = mapped_column(String(50), index=True)
    barcode: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(10), default="goods")  # goods|service
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.product_categories.id", ondelete="SET NULL")
    )
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.uoms.id", ondelete="SET NULL")
    )

    is_purchasable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=True)
    track_inventory: Mapped[bool] = mapped_column(Boolean, default=True)

    sale_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    sale_tax_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.taxes.id", ondelete="SET NULL")
    )
    purchase_tax_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog.taxes.id", ondelete="SET NULL")
    )
    min_stock: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    image_key: Mapped[str | None] = mapped_column(String(300))

    status: Mapped[str] = mapped_column(String(20), default="active")  # active|archived

    category = relationship("ProductCategory", lazy="joined")
    uom = relationship("Uom", lazy="joined")
