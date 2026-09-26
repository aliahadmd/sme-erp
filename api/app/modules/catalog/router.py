"""Catalog API: products, categories, UoM, taxes."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.catalog.models import Product, ProductCategory, Tax, Uom
from app.modules.catalog.schemas import (
    CategoryPage,
    ProductCategoryIn,
    ProductCategoryOut,
    ProductIn,
    ProductOut,
    ProductPage,
    ProductUpdateIn,
    TaxIn,
    TaxOut,
    TaxOutList,
    UomIn,
    UomOut,
    UomOutList,
)
from app.modules.core.deps import CurrentUser, require
from app.modules.core.service import get_organization, write_audit
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/catalog")


# ------------------------------------------------------------------ products
async def _get_product(session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    product = await session.get(Product, product_id)
    if not product or product.org_id != org_id:
        raise NotFoundError("Product not found")
    return product


@router.get("/products", response_model=ProductPage)
async def list_products(
    params: PageParamsDep,
    q: str | None = Query(None, max_length=100),
    category_id: uuid.UUID | None = None,
    type: str | None = Query(None, pattern=r"^(goods|service)$"),
    include_archived: bool = False,
    _user: CurrentUser = Depends(require("catalog.product.read")),
    session: AsyncSession = Depends(get_session),
) -> ProductPage:
    org = await get_organization(session)
    stmt = select(Product).where(Product.org_id == org.id).order_by(Product.name)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if type:
        stmt = stmt.where(Product.type == type)
    if not include_archived:
        stmt = stmt.where(Product.status == "active")
    rows, total = await paginate(session, stmt, params)
    return ProductPage(
        items=[ProductOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: uuid.UUID,
    _user: CurrentUser = Depends(require("catalog.product.read")),
    session: AsyncSession = Depends(get_session),
) -> Product:
    org = await get_organization(session)
    return await _get_product(session, org.id, product_id)


def _sku_from_name(name: str) -> str:
    letters = "".join(ch for ch in name.upper() if ch.isalnum())[:4]
    return f"{letters or 'SKU'}-{uuid.uuid4().hex[:6].upper()}"


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    body: ProductIn,
    user: CurrentUser = Depends(require("catalog.product.create")),
    session: AsyncSession = Depends(get_session),
) -> Product:
    org = await get_organization(session)
    if body.type == "service" and body.track_inventory:
        raise ValidationError("Services cannot track inventory")
    sku = (body.sku or _sku_from_name(body.name)).upper()
    existing = (
        await session.scalars(select(Product).where(Product.org_id == org.id, Product.sku == sku))
    ).first()
    if existing:
        raise ConflictError(f"SKU {sku} already exists")
    product = Product(org_id=org.id, sku=sku, **body.model_dump(exclude={"sku", "tags"}))
    session.add(product)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="catalog.product",
        entity_id=product.id,
        after={"sku": sku, "name": product.name},
    )
    await session.commit()
    await session.refresh(product)
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdateIn,
    user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> Product:
    org = await get_organization(session)
    product = await _get_product(session, org.id, product_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("type") == "service" and data.get("track_inventory", product.track_inventory):
        raise ValidationError("Services cannot track inventory")
    for field, value in data.items():
        setattr(product, field, value)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="catalog.product",
        entity_id=product.id,
        after={"name": product.name},
    )
    await session.commit()
    await session.refresh(product)
    return product


@router.post("/products/{product_id}/archive", response_model=ProductOut)
async def archive_product(
    product_id: uuid.UUID,
    archived: bool = True,
    user: CurrentUser = Depends(require("catalog.product.archive")),
    session: AsyncSession = Depends(get_session),
) -> Product:
    org = await get_organization(session)
    product = await _get_product(session, org.id, product_id)
    product.status = "archived" if archived else "active"
    await write_audit(
        session,
        actor=user.user,
        action="archive" if archived else "unarchive",
        entity_type="catalog.product",
        entity_id=product.id,
    )
    await session.commit()
    await session.refresh(product)
    return product


@router.post("/products/{product_id}/generate-description")
async def generate_product_description(
    product_id: uuid.UUID,
    user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stretch demo: AI-generated product description via the OpenRouter
    plumbing from plan-2. Degrades gracefully when no API key is configured."""
    from app.core.ai import AIClient
    from app.core.config import get_settings
    from app.core.errors import DomainError

    org = await get_organization(session)
    product = await _get_product(session, org.id, product_id)
    client = AIClient(get_settings())
    if not client.enabled:
        raise DomainError(
            "AI is disabled: OPENROUTER_API_KEY is not set",
            code="ai_disabled",
            status_code=503,
        )
    category_name = product.category.name if product.category else None
    try:
        result = await client.complete(
            system=(
                "You write concise, factual e-commerce product descriptions "
                "(2-3 sentences) for an SME ERP. No marketing fluff."
            ),
            prompt=(
                f"Product: {product.name}\n"
                f"Type: {product.type}\n"
                f"Category: {category_name or 'uncategorized'}"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise DomainError("AI request failed", code="ai_error", status_code=502) from exc
    return {"description": result.text, "model": result.model}


@router.post("/products/generate-missing")
async def generate_missing_descriptions(
    user: CurrentUser = Depends(require("catalog.product.update")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue AI description drafts for active products without descriptions.

    The worker generates drafts into the review queue (never auto-applied)."""
    from sqlalchemy import func

    from app.core.config import get_settings
    from app.core.errors import DomainError
    from app.jobs.queue import enqueue

    if not get_settings().ai_enabled:
        raise DomainError(
            "AI is disabled: OPENROUTER_API_KEY is not set", code="ai_disabled", status_code=503
        )
    org = await get_organization(session)
    count = (
        await session.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.org_id == org.id,
                Product.status == "active",
                (Product.description.is_(None)) | (Product.description == ""),
            )
        )
    ) or 0
    await write_audit(
        session,
        actor=user.user,
        action="request",
        entity_type="ai.bulk_descriptions",
        after={"queued": int(count)},
    )
    await session.commit()
    if count:
        await enqueue("generate_missing_descriptions", org_id=str(org.id), limit=int(count))
    return {"queued": int(count)}


# ---------------------------------------------------------------- categories
@router.get("/categories", response_model=CategoryPage)
async def list_categories(
    params: PageParamsDep,
    _user: CurrentUser = Depends(require("catalog.category.read")),
    session: AsyncSession = Depends(get_session),
) -> CategoryPage:
    org = await get_organization(session)
    stmt = (
        select(ProductCategory)
        .where(ProductCategory.org_id == org.id)
        .order_by(ProductCategory.name)
    )
    rows, total = await paginate(session, stmt, params)
    return CategoryPage(
        items=[ProductCategoryOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/categories", response_model=ProductCategoryOut, status_code=201)
async def create_category(
    body: ProductCategoryIn,
    user: CurrentUser = Depends(require("catalog.category.create")),
    session: AsyncSession = Depends(get_session),
) -> ProductCategory:
    org = await get_organization(session)
    category = ProductCategory(org_id=org.id, **body.model_dump())
    session.add(category)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="catalog.category",
        entity_id=category.id,
        after={"name": category.name},
    )
    await session.commit()
    await session.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(
    category_id: uuid.UUID,
    user: CurrentUser = Depends(require("catalog.category.delete")),
    session: AsyncSession = Depends(get_session),
) -> None:
    org = await get_organization(session)
    category = await session.get(ProductCategory, category_id)
    if not category or category.org_id != org.id:
        raise NotFoundError("Category not found")
    in_use = (
        await session.scalars(select(Product.id).where(Product.category_id == category_id).limit(1))
    ).first()
    if in_use:
        raise ConflictError("Category is used by products — remove the link first")
    await write_audit(
        session,
        actor=user.user,
        action="delete",
        entity_type="catalog.category",
        entity_id=category.id,
        before={"name": category.name},
    )
    await session.delete(category)
    await session.commit()


# ---------------------------------------------------------------------- uoms
@router.get("/uoms", response_model=UomOutList)
async def list_uoms(
    _user: CurrentUser = Depends(require("catalog.uom.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Uom]:
    org = await get_organization(session)
    return list(await session.scalars(select(Uom).where(Uom.org_id == org.id).order_by(Uom.code)))


@router.post("/uoms", response_model=UomOut, status_code=201)
async def create_uom(
    body: UomIn,
    user: CurrentUser = Depends(require("catalog.uom.create")),
    session: AsyncSession = Depends(get_session),
) -> Uom:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(Uom).where(Uom.org_id == org.id, Uom.code == body.code.upper())
        )
    ).first()
    if existing:
        raise ConflictError(f"UoM {body.code.upper()} already exists")
    uom = Uom(org_id=org.id, code=body.code.upper(), name=body.name)
    session.add(uom)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="catalog.uom",
        entity_id=uom.id,
        after={"code": uom.code},
    )
    await session.commit()
    await session.refresh(uom)
    return uom


# --------------------------------------------------------------------- taxes
@router.get("/taxes", response_model=TaxOutList)
async def list_taxes(
    _user: CurrentUser = Depends(require("catalog.tax.read")),
    session: AsyncSession = Depends(get_session),
) -> list[Tax]:
    org = await get_organization(session)
    return list(await session.scalars(select(Tax).where(Tax.org_id == org.id).order_by(Tax.code)))


@router.post("/taxes", response_model=TaxOut, status_code=201)
async def create_tax(
    body: TaxIn,
    user: CurrentUser = Depends(require("catalog.tax.create")),
    session: AsyncSession = Depends(get_session),
) -> Tax:
    org = await get_organization(session)
    existing = (
        await session.scalars(
            select(Tax).where(Tax.org_id == org.id, Tax.code == body.code.upper())
        )
    ).first()
    if existing:
        raise ConflictError(f"Tax code {body.code.upper()} already exists")
    tax = Tax(org_id=org.id, code=body.code.upper(), **body.model_dump(exclude={"code"}))
    session.add(tax)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="catalog.tax",
        entity_id=tax.id,
        after={"code": tax.code, "rate": str(tax.rate_pct)},
    )
    await session.commit()
    await session.refresh(tax)
    return tax


@router.patch("/taxes/{tax_id}", response_model=TaxOut)
async def update_tax(
    tax_id: uuid.UUID,
    body: TaxIn,
    user: CurrentUser = Depends(require("catalog.tax.update")),
    session: AsyncSession = Depends(get_session),
) -> Tax:
    org = await get_organization(session)
    tax = await session.get(Tax, tax_id)
    if not tax or tax.org_id != org.id:
        raise NotFoundError("Tax not found")
    for field, value in body.model_dump(exclude_unset=True, exclude={"code"}).items():
        setattr(tax, field, value)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="catalog.tax",
        entity_id=tax.id,
        after={"code": tax.code, "rate": str(tax.rate_pct)},
    )
    await session.commit()
    await session.refresh(tax)
    return tax
