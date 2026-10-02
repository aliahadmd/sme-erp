"""Multi-currency API: currencies + FX rates admin."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError
from app.modules.core.deps import CurrentUser, get_current_user, require
from app.modules.core.service import get_organization, write_audit
from app.modules.currencies.models import Currency, FxRate

router = APIRouter(prefix="/currencies", tags=["currencies"])


class CurrencyIn(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    name: str = Field(min_length=1, max_length=50)


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str


class RateIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    rate_date: date
    rate: Decimal = Field(gt=0)


class RateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    currency: str
    rate_date: date
    rate: Decimal


@router.get("", response_model=list[CurrencyOut])
async def list_currencies(
    # Any signed-in user: document editors need the list for currency pickers.
    _user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Currency]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(Currency).where(Currency.org_id == org.id).order_by(Currency.code)
        )
    )


@router.post("", response_model=CurrencyOut, status_code=201)
async def create_currency(
    body: CurrencyIn,
    user: CurrentUser = Depends(require("core.settings.update")),
    session: AsyncSession = Depends(get_session),
) -> Currency:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(Currency).where(Currency.org_id == org.id, Currency.code == body.code.upper())
        )
    ).first()
    if existing:
        raise ConflictError(f"Currency {body.code.upper()} already exists")
    currency = Currency(org_id=org.id, code=body.code.upper(), name=body.name)
    session.add(currency)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="core.currency",
        entity_id=currency.id,
        after={"code": currency.code},
    )
    await session.commit()
    await session.refresh(currency)
    return currency


@router.get("/rates", response_model=list[RateOut])
async def list_rates(
    _user: CurrentUser = Depends(require("core.settings.read")),
    session: AsyncSession = Depends(get_session),
) -> list[FxRate]:
    org = await get_organization(session)
    return list(
        await session.scalars(
            select(FxRate).where(FxRate.org_id == org.id).order_by(FxRate.rate_date.desc())
        )
    )


@router.put("/rates", response_model=RateOut)
async def upsert_rate(
    body: RateIn,
    user: CurrentUser = Depends(require("core.settings.update")),
    session: AsyncSession = Depends(get_session),
) -> FxRate:
    """Set the rate for a currency on a date (idempotent)."""
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(FxRate).where(
                FxRate.org_id == org.id,
                FxRate.currency == body.currency.upper(),
                FxRate.rate_date == body.rate_date,
            )
        )
    ).first()
    if existing:
        existing.rate = body.rate
        rate = existing
    else:
        rate = FxRate(
            org_id=org.id, currency=body.currency.upper(), rate_date=body.rate_date, rate=body.rate
        )
        session.add(rate)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="core.fx_rate",
        entity_id=rate.id,
        after={"currency": rate.currency, "rate": str(rate.rate)},
    )
    await session.commit()
    await session.refresh(rate)
    return rate
