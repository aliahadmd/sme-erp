"""AI feature endpoints — bulk descriptions, report summarizer, search.

All AI content lands in a review queue (`core.ai_drafts`); nothing is applied
automatically. Search is keyword (ILIKE) matching over products — embedding
(pgvector) search is not implemented yet.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import AIClient
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.models import AiDraft
from app.modules.core.service import get_organization, write_audit

router = APIRouter(prefix="/ai")


def _ai_client() -> AIClient:
    client = AIClient(get_settings())
    if not client.enabled:
        raise DomainError(
            "AI is disabled: OPENROUTER_API_KEY is not set", code="ai_disabled", status_code=503
        )
    return client


# Fields an accepted product draft may write to.
DRAFT_FIELDS = {"description"}


async def _check_budget(request: Request) -> None:
    """Daily AI request budget (per deployment). Exceeding it returns 429."""
    limit = get_settings().ai_daily_request_limit
    key = f"ai:budget:{date.today().isoformat()}"
    count = await request.app.state.redis.incr(key)
    if count == 1:
        await request.app.state.redis.expire(key, 60 * 60 * 48)
    if count > limit:
        raise DomainError(
            "AI daily request budget exceeded", code="ai_budget_exceeded", status_code=429
        )


# ---------------------------------------------------------------- summaries
class SummarizeIn(BaseModel):
    report: str  # dashboard | sales | purchases | aging | tax
    payload: dict


@router.post("/summarize")
async def summarize_report(
    body: SummarizeIn,
    request: Request,
    _user: CurrentUser = Depends(require("reports.view")),
) -> dict:
    client = _ai_client()  # disabled → 503 without consuming budget
    await _check_budget(request)
    try:
        result = await client.complete(
            system=(
                "You are a financial analyst for an SME. Summarize the following "
                "report data in 3-5 short bullet-style observations for a business "
                "owner. Be concrete about numbers. No preamble."
            ),
            prompt=f"Report: {body.report}\nData: {body.payload}",
        )
    except Exception as exc:  # noqa: BLE001
        raise DomainError("AI request failed", code="ai_error", status_code=502) from exc
    return {"summary": result.text, "model": result.model}


# ------------------------------------------------------------------ drafts
@router.get("/drafts")
async def list_drafts(
    _user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    org = await get_organization(session)
    rows = (
        await session.scalars(
            select(AiDraft)
            .where(AiDraft.org_id == org.id, AiDraft.status == "pending")
            .order_by(AiDraft.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(d.id),
            "entity_type": d.entity_type,
            "entity_id": str(d.entity_id),
            "field": d.field,
            "draft_text": d.draft_text,
            "model": d.model,
        }
        for d in rows
    ]


@router.post("/drafts/{draft_id}/accept")
async def accept_draft(
    draft_id: uuid.UUID,
    user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.modules.catalog.models import Product

    org = await get_organization(session)
    draft = await session.get(AiDraft, draft_id)
    if not draft or draft.org_id != org.id:
        raise NotFoundError("Draft not found")
    if draft.status != "pending":
        raise ConflictError(f"Draft is already {draft.status}")
    if draft.entity_type != "product" or (draft.field or "description") not in DRAFT_FIELDS:
        raise NotFoundError("Unsupported draft entity or field")
    product = await session.get(Product, draft.entity_id)
    if not product or product.org_id != org.id:
        raise NotFoundError("Product no longer exists")
    setattr(product, draft.field or "description", draft.draft_text)
    draft.status = "accepted"
    await write_audit(
        session, actor=user.user, action="accept", entity_type="ai.draft", entity_id=draft.id
    )
    await session.commit()
    return {"status": "accepted"}


@router.post("/drafts/{draft_id}/discard")
async def discard_draft(
    draft_id: uuid.UUID,
    user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    org = await get_organization(session)
    draft = await session.get(AiDraft, draft_id)
    if not draft or draft.org_id != org.id:
        raise NotFoundError("Draft not found")
    if draft.status != "pending":
        raise ConflictError(f"Draft is already {draft.status}")
    draft.status = "discarded"
    await write_audit(
        session, actor=user.user, action="discard", entity_type="ai.draft", entity_id=draft.id
    )
    await session.commit()
    return {"status": "discarded"}


# ------------------------------------------------------------------ search
@router.get("/search")
async def semantic_search(
    q: str,
    _user: CurrentUser = Depends(require("reports.view")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Keyword search over active products (name, SKU, description)."""
    org = await get_organization(session)
    like = f"%{q.lower()}%"
    from app.modules.catalog.models import Product

    prod_rows = (
        await session.scalars(
            select(Product).where(
                Product.org_id == org.id,
                Product.status == "active",
                (Product.name.ilike(like))
                | (Product.sku.ilike(like))
                | (Product.description.ilike(like)),
            )
        )
    ).all()
    return [
        {
            "entity": "product",
            "id": str(p.id),
            "title": p.name,
            "detail": p.sku,
        }
        for p in prod_rows
    ]
