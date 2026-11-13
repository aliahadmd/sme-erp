import uuid
from decimal import localcontext
from types import SimpleNamespace

from app.shared.order_progress import remaining_by_product
from app.shared.pagination import Page, PageParamsDep
from fastapi import APIRouter, Depends, Query, Request

from erp_cf.command_bus import invoke
from erp_cf.db import D1Database
from erp_cf.document_reads import read_documents
from erp_cf.documents import PURCHASE, QUOTE, SALES
from erp_cf.identity import current_actor, environment, require


def build_router(kind, prefix):
    router = APIRouter(prefix=prefix, dependencies=[Depends(current_actor)])

    async def read(request, **kwargs):
        env = environment(request)
        return await read_documents(D1Database(env.DB), str(env.ORG_ID), kind, **kwargs)

    @router.get(
        "",
        response_model=Page[kind.out],
        dependencies=[Depends(require(f"{kind.permission}.read"))],
    )
    async def documents(
        params: PageParamsDep,
        request: Request,
        status: str | None = None,
        q: str | None = Query(None, max_length=60),
        party_id: uuid.UUID | None = None,
    ):
        return await read(
            request, limit=params.limit, offset=params.offset, status=status, q=q, party_id=party_id
        )

    @router.get(
        "/{identity}",
        response_model=kind.out,
        dependencies=[Depends(require(f"{kind.permission}.read"))],
    )
    async def document(identity: uuid.UUID, request: Request):
        return await read(request, identity=identity)

    @router.post("", response_model=kind.out, status_code=201)
    async def create_document(body: kind.create, request: Request):
        return await invoke(request, f"{kind.permission}.create", {"body": body})

    @router.patch("/{identity}", response_model=kind.out)
    async def update_document(identity: uuid.UUID, body: kind.update, request: Request):
        return await invoke(
            request,
            f"{kind.permission}.update",
            {"id": identity, "body": body.model_dump(exclude_unset=True)},
        )

    def transition(action):
        async def perform(identity: uuid.UUID, request: Request):
            return await invoke(request, f"{kind.permission}.{action}", {"id": identity})

        return perform

    for action in (
        ["send", "accept", "reject", "cancel", "convert"]
        if kind is QUOTE
        else ["confirm", "cancel"]
    ):
        router.add_api_route(
            "/{identity}/" + action,
            transition(action),
            methods=["POST"],
            response_model=None if action == "convert" else kind.out,
        )

    if kind is not QUOTE:

        @router.get(
            "/{identity}/outstanding", dependencies=[Depends(require(f"{kind.permission}.read"))]
        )
        async def outstanding(identity: uuid.UUID, request: Request):
            row = await read(request, identity=identity)
            document = SimpleNamespace(
                **{**row, "lines": [SimpleNamespace(**r) for r in row["lines"]]}
            )
            with localcontext() as context:
                context.prec = 80
                remaining = remaining_by_product(document, kind.move)
            return {
                "order_id": row["id"],
                "number": row["number"],
                "outstanding": {p: str(q) for p, q in remaining.items() if q > 0},
            }

    return router


routers = [
    build_router(SALES, "/api/sales/orders"),
    build_router(PURCHASE, "/api/purchasing/orders"),
    build_router(QUOTE, "/api/sales/quotations"),
]
