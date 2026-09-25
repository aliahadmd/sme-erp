"""Pagination primitives shared by all list endpoints."""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PageParams:
    limit: int = 20
    offset: int = 0


def page_params(
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


async def paginate(
    session: AsyncSession, stmt: Select, params: PageParams
) -> tuple[list[Any], int]:
    """Execute a statement with limit/offset; returns (rows, total_count)."""
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total: int = (await session.scalar(count_stmt)) or 0
    rows = list((await session.scalars(stmt.limit(params.limit).offset(params.offset))).all())
    return rows, total
