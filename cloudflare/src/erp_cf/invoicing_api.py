import uuid

from app.modules.invoicing import schemas
from app.shared.pagination import PageParamsDep
from fastapi import APIRouter, Depends, Query, Request

from erp_cf.command_bus import invoke
from erp_cf.db import D1Database
from erp_cf.identity import current_actor, environment, require
from erp_cf.invoicing_reads import invoice_page, payment_page, statement

router = APIRouter(prefix="/api/invoicing", dependencies=[Depends(current_actor)])


def connection(request):
    env = environment(request)
    return D1Database(env.DB), str(env.ORG_ID)


@router.get(
    "/invoices",
    response_model=schemas.InvoicePage,
    dependencies=[Depends(require("invoicing.invoice.read"))],
)
async def invoices(
    params: PageParamsDep,
    request: Request,
    invoice_type: str | None = Query(None, pattern=r"^(ar|ap|ar_credit|ap_credit)$"),
    side: str | None = Query(None, pattern=r"^(ar|ap)$"),
    status: str | None = None,
    party_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=60),
):
    filters = []
    if invoice_type:
        filters.append(("invoice_type", invoice_type))
    if side:
        filters.append(("invoice_type", [side, side + "_credit"]))
    return await invoice_page(
        *connection(request),
        limit=params.limit,
        offset=params.offset,
        status=status,
        party_id=party_id,
        q=q,
        filters=filters,
    )


@router.get(
    "/invoices/{identity}",
    response_model=schemas.InvoiceOut,
    dependencies=[Depends(require("invoicing.invoice.read"))],
)
async def invoice(identity: uuid.UUID, request: Request):
    return await invoice_page(*connection(request), identity=identity)


@router.post("/invoices", response_model=schemas.InvoiceOut, status_code=201)
async def create_invoice(body: schemas.InvoiceCreateIn, request: Request):
    return await invoke(request, "invoicing.invoice.create", {"body": body})


@router.patch("/invoices/{identity}", response_model=schemas.InvoiceOut)
async def update_invoice(identity: uuid.UUID, body: schemas.InvoiceUpdateIn, request: Request):
    return await invoke(
        request,
        "invoicing.invoice.update",
        {"id": identity, "body": body.model_dump(exclude_unset=True)},
    )


@router.delete("/invoices/{identity}", status_code=204)
async def delete_invoice(identity: uuid.UUID, request: Request):
    return await invoke(request, "invoicing.invoice.delete", {"id": identity})


def invoice_action(action):
    async def perform(identity: uuid.UUID, request: Request):
        return await invoke(request, "invoicing.invoice." + action, {"id": identity})

    return perform


for action, suffix in [("post", "post"), ("void", "void"), ("email", "send-email")]:
    router.add_api_route(
        "/invoices/{identity}/" + suffix,
        invoice_action(action),
        methods=["POST"],
        response_model=None if action == "email" else schemas.InvoiceOut,
    )


@router.get(
    "/payments",
    response_model=schemas.PaymentPage,
    dependencies=[Depends(require("invoicing.payment.read"))],
)
async def payments(
    params: PageParamsDep,
    request: Request,
    direction: str | None = Query(None, pattern=r"^(in|out)$"),
    party_id: uuid.UUID | None = None,
):
    return await payment_page(
        *connection(request),
        limit=params.limit,
        offset=params.offset,
        party_id=party_id,
        filters=[("direction", direction)] if direction else [],
    )


@router.post("/payments", response_model=schemas.PaymentOut, status_code=201)
async def create_payment(body: schemas.PaymentIn, request: Request):
    return await invoke(request, "invoicing.payment.create", {"body": body})


@router.post("/payments/{identity}/allocate", response_model=schemas.PaymentOut)
async def allocate_payment(
    identity: uuid.UUID, body: schemas.PaymentAllocationsIn, request: Request
):
    return await invoke(request, "invoicing.payment.allocate", {"id": identity, "body": body})


@router.post("/payments/{identity}/void", response_model=schemas.PaymentOut)
async def void_payment(identity: uuid.UUID, request: Request):
    return await invoke(request, "invoicing.payment.void", {"id": identity})


@router.get(
    "/statement/{party_id}",
    response_model=schemas.StatementOut,
    dependencies=[Depends(require("invoicing.invoice.read"))],
)
async def party_statement(party_id: uuid.UUID, request: Request):
    return await statement(*connection(request), str(party_id))
