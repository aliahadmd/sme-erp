import uuid

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
    UomIn,
    UomOut,
)
from app.modules.crm.schemas import (
    ContactArchiveIn,
    ContactIn,
    ContactOut,
    ContactPage,
    ContactUpdateIn,
)
from app.modules.currencies.schemas import CurrencyIn, CurrencyOut, RateIn, RateOut
from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Query, Request

from erp_cf.command_bus import invoke
from erp_cf.core_api import repository
from erp_cf.identity import current_actor, require

router = APIRouter(prefix="/api", dependencies=[Depends(current_actor)])


@router.get(
    "/crm/contacts", response_model=ContactPage, dependencies=[Depends(require("crm.contact.read"))]
)
async def contacts(
    params: PageParamsDep,
    request: Request,
    type: str | None = Query(None, pattern=r"^(customer|supplier)$"),
    q: str | None = Query(None, max_length=100),
    tag: str | None = None,
    include_archived: bool = False,
):
    conditions, values = ["1"], []
    if type:
        conditions.append("is_customer=1" if type == "customer" else "is_supplier=1")
    if q:
        conditions.append("(_name_fold LIKE ? OR _code_fold LIKE ? OR _legal_fold LIKE ?)")
        values.extend(["%" + q.casefold() + "%"] * 3)
    if tag:
        conditions.append("EXISTS(SELECT 1 FROM json_each(tags) WHERE value=?)")
        values.append(tag)
    if not include_archived:
        conditions.append("status='active'")
    return await repository(request, "crm_contacts").page(
        params.limit,
        params.offset,
        where=" AND ".join(conditions),
        params=tuple(values),
        order_by="name",
    )


@router.get(
    "/crm/contacts/{identity}",
    response_model=ContactOut,
    dependencies=[Depends(require("crm.contact.read"))],
)
async def contact(identity: uuid.UUID, request: Request):
    return await repository(request, "crm_contacts").get(str(identity))


@router.post("/crm/contacts", response_model=ContactOut, status_code=201)
async def create_contact(body: ContactIn, request: Request):
    return await invoke(request, "crm.contact.create", {"body": body})


@router.patch("/crm/contacts/{identity}", response_model=ContactOut)
async def update_contact(identity: uuid.UUID, body: ContactUpdateIn, request: Request):
    return await invoke(
        request, "crm.contact.update", {"id": identity, "body": body.model_dump(exclude_unset=True)}
    )


@router.post("/crm/contacts/{identity}/archive", response_model=ContactOut)
async def archive_contact(identity: uuid.UUID, body: ContactArchiveIn, request: Request):
    return await invoke(request, "crm.contact.archive", {"id": identity, "body": body})


@router.delete("/crm/contacts/{identity}", status_code=204)
async def delete_contact(identity: uuid.UUID, request: Request):
    return await invoke(request, "crm.contact.delete", {"id": identity})


@router.get(
    "/catalog/products",
    response_model=ProductPage,
    dependencies=[Depends(require("catalog.product.read"))],
)
async def products(
    params: PageParamsDep,
    request: Request,
    q: str | None = Query(None, max_length=100),
    category_id: uuid.UUID | None = None,
    type: str | None = Query(None, pattern=r"^(goods|service)$"),
    include_archived: bool = False,
):
    conditions, values = ["1"], []
    if q:
        conditions.append("(_name_fold LIKE ? OR _sku_fold LIKE ?)")
        values.extend(["%" + q.casefold() + "%"] * 2)
    if category_id:
        conditions.append("category_id=?")
        values.append(str(category_id))
    if type:
        conditions.append("type=?")
        values.append(type)
    if not include_archived:
        conditions.append("status='active'")
    return await repository(request, "catalog_products").page(
        params.limit,
        params.offset,
        where=" AND ".join(conditions),
        params=tuple(values),
        order_by="name",
    )


@router.get(
    "/catalog/products/{identity}",
    response_model=ProductOut,
    dependencies=[Depends(require("catalog.product.read"))],
)
async def product(identity: uuid.UUID, request: Request):
    return await repository(request, "catalog_products").get(str(identity))


@router.post("/catalog/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductIn, request: Request):
    return await invoke(request, "catalog.product.create", {"body": body})


@router.patch("/catalog/products/{identity}", response_model=ProductOut)
async def update_product(identity: uuid.UUID, body: ProductUpdateIn, request: Request):
    return await invoke(
        request,
        "catalog.product.update",
        {"id": identity, "body": body.model_dump(exclude_unset=True)},
    )


@router.post("/catalog/products/{identity}/archive", response_model=ProductOut)
async def archive_product(identity: uuid.UUID, request: Request, archived: bool = True):
    return await invoke(request, "catalog.product.archive", {"id": identity, "archived": archived})


@router.get(
    "/catalog/categories",
    response_model=CategoryPage,
    dependencies=[Depends(require("catalog.category.read"))],
)
async def categories(params: PageParamsDep, request: Request):
    return await repository(request, "catalog_product_categories").page(
        params.limit, params.offset, order_by="name"
    )


@router.post("/catalog/categories", response_model=ProductCategoryOut, status_code=201)
async def create_category(body: ProductCategoryIn, request: Request):
    return await invoke(request, "catalog.category.create", {"body": body})


@router.delete("/catalog/categories/{identity}", status_code=204)
async def delete_category(identity: uuid.UUID, request: Request):
    return await invoke(request, "catalog.category.delete", {"id": identity})


@router.get(
    "/catalog/uoms",
    response_model=list[UomOut],
    dependencies=[Depends(require("catalog.uom.read"))],
)
async def uoms(request: Request):
    return await repository(request, "catalog_uoms").find(order_by="code")


@router.post("/catalog/uoms", response_model=UomOut, status_code=201)
async def create_uom(body: UomIn, request: Request):
    return await invoke(request, "catalog.uom.create", {"body": body})


@router.get(
    "/catalog/taxes",
    response_model=list[TaxOut],
    dependencies=[Depends(require("catalog.tax.read"))],
)
async def taxes(request: Request):
    return await repository(request, "catalog_taxes").find(order_by="code")


@router.post("/catalog/taxes", response_model=TaxOut, status_code=201)
async def create_tax(body: TaxIn, request: Request):
    return await invoke(request, "catalog.tax.create", {"body": body})


@router.patch("/catalog/taxes/{identity}", response_model=TaxOut)
async def update_tax(identity: uuid.UUID, body: TaxIn, request: Request):
    return await invoke(
        request, "catalog.tax.update", {"id": identity, "body": body.model_dump(exclude_unset=True)}
    )


@router.get("/currencies", response_model=list[CurrencyOut])
async def currencies(request: Request):
    return await repository(request, "core_currencies").find(order_by="code")


@router.post("/currencies", response_model=CurrencyOut, status_code=201)
async def create_currency(body: CurrencyIn, request: Request):
    return await invoke(request, "core.currency.create", {"body": body})


@router.get(
    "/currencies/rates",
    response_model=list[RateOut],
    dependencies=[Depends(require("core.settings.read"))],
)
async def rates(request: Request):
    return await repository(request, "core_fx_rates").find(order_by="rate_date DESC")


@router.put("/currencies/rates", response_model=RateOut)
async def put_rate(body: RateIn, request: Request):
    return await invoke(request, "core.fx_rate.update", {"body": body})
