import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.core.models import Organization
from app.shared.numbering import next_number


async def _org_id(session) -> object:
    return (await session.scalars(select(Organization).limit(1))).first().id


async def test_numbering_increments(db_session):
    org = (await db_session.scalars(select(Organization).limit(1))).first()
    n1 = await next_number(db_session, org.id, "numbering_so_test", "SO")
    n2 = await next_number(db_session, org.id, "numbering_so_test", "SO")
    assert n1.endswith("0001")
    assert n2.endswith("0002")
    assert n1.startswith("SO-")


async def test_numbering_entities_are_independent(db_session):
    org = (await db_session.scalars(select(Organization).limit(1))).first()
    so = await next_number(db_session, org.id, "numbering_so_test", "SO")
    po = await next_number(db_session, org.id, "numbering_po_test", "PO")
    assert so.endswith("0001") and po.endswith("0001")


async def test_numbering_concurrent_sessions_never_collide(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as reader:
        org = (await reader.scalars(select(Organization).limit(1))).first()
        await reader.commit()

    async def take() -> str:
        async with factory() as session:
            number = await next_number(session, org.id, "concurrent_test", "CT")
            await session.commit()
            return number

    results = await asyncio.gather(*(take() for _ in range(8)))
    assert len(set(results)) == 8, f"collisions: {results}"
