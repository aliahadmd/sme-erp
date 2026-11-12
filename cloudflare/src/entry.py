"""Initial runtime probe; financial command paths are added behind the proof gate."""

from decimal import ROUND_HALF_EVEN, Decimal

from fastapi import FastAPI, Request
from pydantic import BaseModel
from workers import asgi

from erp_cf.coordinator import OrganizationCommands as OrganizationCommands
from erp_cf.proof import router as proof_router

app = FastAPI(title="ERP Cloudflare feasibility", docs_url=None, redoc_url=None)
app.include_router(proof_router)


class Health(BaseModel):
    status: str
    runtime: str
    decimal_tie: str


@app.get("/healthz", response_model=Health)
async def health(request: Request) -> Health:
    row = await request.scope["env"].DB.prepare("SELECT 1 AS ready").first()
    return Health(
        status="ok" if row.ready == 1 else "down",
        runtime="python-workers-fastapi",
        decimal_tie=str(Decimal("1.005").quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)),
    )


Default = asgi.entrypoint(app)
