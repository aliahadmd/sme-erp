"""FX rate resolution + document base-currency helpers."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.core.service import get_organization
from app.modules.currencies.models import FxRate


async def resolve_rate(
    session: AsyncSession, org_id: uuid.UUID, currency: str, on_date: date
) -> Decimal:
    """Rate (currency per 1 base) effective on a date: exact → latest earlier.

    Base currency resolves to 1.
    """
    org = await get_organization(session)
    if currency == org.base_currency:
        return Decimal("1")
    result = await session.scalars(
        select(FxRate.rate)
        .where(
            FxRate.org_id == org_id,
            FxRate.currency == currency,
            FxRate.rate_date <= on_date,
        )
        .order_by(FxRate.rate_date.desc())
        .limit(1)
    )
    rate = result.first()
    if rate is None:
        raise ValidationError(
            f"No FX rate for {currency} on or before {on_date} — add one in Settings → Currencies"
        )
    return Decimal(str(rate))


def to_base(amount: Decimal, rate: Decimal) -> Decimal:
    """Document-currency amount → base currency at the snapshot rate."""
    return (Decimal(str(amount)) / Decimal(str(rate))).quantize(Decimal("0.01"))
