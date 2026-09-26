"""Multi-currency: currencies and FX rates. Schema `core` (shared kernel)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TimestampMixin, UuidPk


class Currency(Base, TimestampMixin, UuidPk):
    __tablename__ = "currencies"
    __table_args__ = (UniqueConstraint("org_id", "code"), {"schema": "core"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(50))


class FxRate(Base, TimestampMixin, UuidPk):
    """Units of `currency` per 1 unit of the org base currency.

    Example: base USD, EUR rate 0.92 on 2026-09-27 → 1 USD = 0.92 EUR.
    amount_base = amount_document / rate.
    """

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("org_id", "currency", "rate_date"), {"schema": "core"})

    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    rate_date: Mapped[date] = mapped_column(Date, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
