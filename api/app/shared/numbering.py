"""Document numbering — atomic per org + entity + year sequences.

Uses INSERT … ON CONFLICT … DO UPDATE … RETURNING so concurrent callers can
never collide. Sequences are gap-tolerant by design (rolled-back transactions
consume their number).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7


async def next_number(session: AsyncSession, org_id: uuid.UUID, entity: str, prefix: str) -> str:
    year = datetime.now(UTC).year
    result = await session.execute(
        text(
            """
            INSERT INTO core.document_sequences (id, org_id, entity, year, last_number)
            VALUES (:id, :org_id, :entity, :year, 1)
            ON CONFLICT (org_id, entity, year)
                DO UPDATE SET last_number = core.document_sequences.last_number + 1
            RETURNING last_number
            """
        ),
        {"id": str(uuid7()), "org_id": str(org_id), "entity": entity, "year": year},
    )
    number = result.scalar_one()
    return f"{prefix}-{year}-{number:04d}"
