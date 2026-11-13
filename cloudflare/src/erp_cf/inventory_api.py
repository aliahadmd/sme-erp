import uuid
from decimal import localcontext

from app.modules.inventory import schemas
from app.shared.pagination import Page, PageParamsDep
from fastapi import APIRouter, Depends, Request

from erp_cf.command_bus import invoke
from erp_cf.core_api import repository
from erp_cf.db import D1Database, Statement, plain
from erp_cf.document_reads import read_documents
from erp_cf.identity import current_actor, environment, require
from erp_cf.inventory_commands import ADJUSTMENT, KINDS
from erp_cf.money import from_coefficient, money

router = APIRouter(prefix="/api/inventory", dependencies=[Depends(current_actor)])


@router.get(
    "/warehouses",
    response_model=list[schemas.WarehouseOut],
    dependencies=[Depends(require("inventory.warehouse.read"))],
)
async def warehouses(request: Request):
    return await repository(request, "inventory_warehouses").find(order_by="code")


@router.post("/warehouses", response_model=schemas.WarehouseOut, status_code=201)
async def create_warehouse(body: schemas.WarehouseIn, request: Request):
    return await invoke(request, "inventory.warehouse.create", {"body": body})


@router.get(
    "/stock",
    response_model=list[schemas.StockRowOut],
    dependencies=[Depends(require("inventory.stock.read"))],
)
async def stock(request: Request, warehouse_id: uuid.UUID | None = None, low_only: bool = False):
    env = environment(request)
    db = D1Database(env.DB)
    params = [str(env.ORG_ID)]
    columns = (
        "p.id AS product_id,p.sku AS product_sku,p.name AS product_name,w.id AS warehouse_id,"
        "w.code AS warehouse_code,CAST(p.min_stock AS TEXT) AS minimum,"
        "CAST(COALESCE(s.qty_on_hand,0) AS TEXT) AS qty,"
        "CAST(COALESCE(s.avg_cost,0) AS TEXT) AS cost "
    )
    if low_only:
        sql = (
            "SELECT "
            + columns
            + (
                "FROM catalog_products p CROSS JOIN inventory_warehouses w "
                "LEFT JOIN inventory_stock s ON s.product_id=p.id "
                "AND s.warehouse_id=w.id AND s.org_id=p.org_id "
                "WHERE p.org_id=? AND w.org_id=p.org_id AND p.status='active' "
                "AND p.type='goods' AND p.track_inventory=1 AND p.min_stock>0 "
                "AND COALESCE(s.qty_on_hand,0)<=p.min_stock"
            )
        )
    else:
        sql = (
            "SELECT "
            + columns
            + (
                "FROM inventory_stock s JOIN catalog_products p "
                "ON p.id=s.product_id AND p.org_id=s.org_id "
                "JOIN inventory_warehouses w ON w.id=s.warehouse_id AND w.org_id=s.org_id "
                "WHERE s.org_id=? AND s.qty_on_hand!=0"
            )
        )
    if warehouse_id:
        sql += " AND w.id=?"
        params.append(str(warehouse_id))
    rows = await db.all(sql + " ORDER BY p.name,w.code", tuple(params))
    result = []
    with localcontext() as context:
        context.prec = 80
        for row in rows:
            qty, cost = from_coefficient(row["qty"], 4), from_coefficient(row["cost"], 6)
            minimum = from_coefficient(row["minimum"], 4)
            result.append(
                schemas.StockRowOut(
                    **{k: v for k, v in row.items() if k not in {"qty", "cost", "minimum"}},
                    qty_on_hand=qty,
                    avg_cost=cost,
                    stock_value=money(qty * cost),
                    is_low=minimum > 0 and qty <= minimum,
                )
            )
    return result


@router.get(
    "/moves",
    response_model=schemas.MovePage,
    dependencies=[Depends(require("inventory.stock.read"))],
)
async def moves(
    params: PageParamsDep,
    request: Request,
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
):
    repo = repository(request, "inventory_stock_moves")
    conditions, values = ["org_id=?"], [repo.org_id]
    for key, value in [("product_id", product_id), ("warehouse_id", warehouse_id)]:
        if value:
            conditions.append(key + "=?")
            values.append(str(value))
    where = " AND ".join(conditions)
    results = await repo.db.batch(
        [
            Statement(
                f"SELECT {repo.table.projection()} FROM inventory_stock_moves WHERE {where} "
                "ORDER BY moved_at DESC,id DESC LIMIT ? OFFSET ?",
                (*values, params.limit, params.offset),
            ),
            Statement(
                f"SELECT CAST(COUNT(*) AS TEXT) AS total FROM inventory_stock_moves WHERE {where}",
                tuple(values),
            ),
        ]
    )
    return {
        "items": [repo.table.decode(plain(r)) for r in results[0]["results"]],
        "total": int(plain(results[1]["results"][0])["total"]),
        "limit": params.limit,
        "offset": params.offset,
    }


def document_router(kind):
    routes = APIRouter(prefix="/" + kind.table.removeprefix("inventory_"))

    async def read(request, **kwargs):
        env = environment(request)
        return await read_documents(D1Database(env.DB), str(env.ORG_ID), kind, **kwargs)

    @routes.get(
        "",
        response_model=Page[kind.out],
        dependencies=[Depends(require(f"{kind.permission}.read"))],
    )
    async def documents(params: PageParamsDep, request: Request, status: str | None = None):
        return await read(request, limit=params.limit, offset=params.offset, status=status)

    if kind is not ADJUSTMENT:

        @routes.get(
            "/{identity}",
            response_model=kind.out,
            dependencies=[Depends(require(f"{kind.permission}.read"))],
        )
        async def document(identity: uuid.UUID, request: Request):
            return await read(request, identity=identity)

    @routes.post("", response_model=kind.out, status_code=201)
    async def create(body: kind.create, request: Request):
        return await invoke(request, f"{kind.permission}.create", {"body": body})

    def transition(action):
        async def perform(identity: uuid.UUID, request: Request):
            return await invoke(request, f"{kind.permission}.{action}", {"id": identity})

        return perform

    for action in ["post"] if kind is ADJUSTMENT else ["post", "void"]:
        routes.add_api_route(
            "/{identity}/" + action, transition(action), methods=["POST"], response_model=kind.out
        )
    return routes


for kind in KINDS:
    router.include_router(document_router(kind))
