"""Authenticated, staging-only checks for the actual Workers/D1 boundary.

Uses isolated proof tables and synthetic input. Never mount in the final ERP
environment. Test failures are intentionally injectable only behind this gate.
"""

import hmac
import json
import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from erp_cf.db import AtomicCommands, CommandConflict, D1Database, Statement, assertion
from erp_cf.money import coefficient, from_coefficient

router = APIRouter(prefix="/proof")


def environment(request: Request):
    env = request.scope["env"]
    if env.ENVIRONMENT != "feasibility":
        raise HTTPException(404)
    expected = getattr(env, "PROOF_TOKEN", "")
    provided = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not expected or not hmac.compare_digest(provided.encode(), str(expected).encode()):
        raise HTTPException(401, "Proof credential required")
    return env


class PrecisionInput(BaseModel):
    value: str = Field(max_length=64)
    scale: int = Field(ge=0, le=8)


@router.post("/precision")
async def precision(body: PrecisionInput, env=Depends(environment)):
    from sqlalchemy import Column, MetaData, String, Table, select
    from sqlalchemy.orm import Session
    from sqlalchemy_cloudflare_d1 import create_engine_from_binding

    from erp_cf.orm_types import ScaledDecimal

    try:
        encoded = coefficient(body.value, body.scale)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db = D1Database(env.DB)
    identity = secrets.token_hex(16)
    await db.batch(
        [
            Statement(
                "INSERT INTO proof_precision VALUES (?, CAST(? AS INTEGER))", (identity, encoded)
            )
        ]
    )
    row = await db.first(
        "SELECT CAST(amount AS TEXT) AS amount FROM proof_precision WHERE id = ?", (identity,)
    )
    table = Table(
        "proof_precision",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("amount", ScaledDecimal(body.scale)),
    )
    # Engine and session are request-local; no connection/cursor crosses requests.
    engine = create_engine_from_binding(env.DB)
    orm_identity = secrets.token_hex(16)
    try:
        with Session(engine) as session:
            orm_value = session.scalar(select(table.c.amount).where(table.c.id == identity))
            # A single ORM INSERT is permitted; this is deliberately not an
            # ORM transaction containing multiple business effects.
            session.execute(
                table.insert().values(id=orm_identity, amount=from_coefficient(encoded, body.scale))
            )
            session.commit()
    finally:
        engine.dispose()
    orm_written = await db.first(
        "SELECT CAST(amount AS TEXT) AS amount FROM proof_precision WHERE id=?", (orm_identity,)
    )
    return {
        "coefficient": row["amount"],
        "native": format(from_coefficient(row["amount"], body.scale), "f"),
        "orm": format(orm_value, "f"),
        "orm_decimal": isinstance(orm_value, Decimal),
        "orm_written_coefficient": orm_written["amount"],
        "matches": coefficient(orm_value, body.scale) == encoded,
    }


class StockInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=0, le=1000000)


@router.post("/stock")
async def stock(body: StockInput, env=Depends(environment)):
    await D1Database(env.DB).batch(
        [Statement("INSERT INTO proof_stock VALUES (?, ?, 0)", (body.id, body.quantity))]
    )
    return {"id": body.id, "quantity": body.quantity}


class PostInput(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    stock_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=1000000)
    total: str = Field(max_length=64)
    fail_after: int | None = Field(default=None, ge=0, le=9)


@router.post("/post")
async def post(body: PostInput, env=Depends(environment)):
    # Public proof calls traverse the same per-organization command gate that
    # will coordinate the migrated financial services.
    result = await env.COMMANDS.getByName("proof").post_command(body.model_dump())
    return JSONResponse(status_code=result["status"], content=result["body"])


@router.post("/post-direct")
async def post_direct(body: PostInput, env=Depends(environment)):
    # Authenticated feasibility-only bypass proves D1 guards independently of
    # the coordinator. This route is never mounted in the final ERP.
    return await execute_post(body, env)


async def execute_post(body: PostInput, env):
    db = D1Database(env.DB)
    commands = AtomicCommands(db)
    identity = commands.identity("proof", "proof", body.key)
    request_hash = commands.request_hash("post", body.model_dump())
    try:
        existing = await commands.replay(identity, request_hash)
    except CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if existing is not None:
        return existing
    current = await db.first("SELECT * FROM proof_stock WHERE id = ?", (body.stock_id,))
    if current is None:
        raise HTTPException(404, "Proof stock does not exist")
    try:
        total = coefficient(body.total, 2)
        if int(total) < 0:
            raise ValueError("Invoice total must be nonnegative")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    invoice_id = "invoice-" + identity
    response = {"invoice_id": invoice_id, "command_id": identity, "total": body.total}
    statements = [
        assertion(
            identity,
            "EXISTS(SELECT 1 FROM proof_stock WHERE id=? AND revision=? AND quantity>=?)",
            (body.stock_id, current["revision"], body.quantity),
        ),
        Statement(
            "UPDATE proof_stock SET quantity=quantity-?, revision=revision+1 WHERE id=?",
            (body.quantity, body.stock_id),
        ),
        Statement(
            "INSERT INTO proof_invoices VALUES (?, CAST(? AS INTEGER), 'posted')",
            (invoice_id, total),
        ),
        Statement(
            "INSERT INTO proof_journal VALUES (?, 'receivable', CAST(? AS INTEGER), 0)",
            (invoice_id, total),
        ),
        Statement(
            "INSERT INTO proof_journal VALUES (?, 'revenue', 0, CAST(? AS INTEGER))",
            (invoice_id, total),
        ),
        assertion(
            identity,
            "(SELECT SUM(debit)-SUM(credit) FROM proof_journal WHERE id=?)=0",
            (invoice_id,),
        ),
        Statement("INSERT INTO proof_audit VALUES (?, ?, 'post')", (identity, invoice_id)),
        Statement(
            "INSERT INTO cf_outbox (id,command_id,event_type,payload_json) VALUES (?,?,?,?)",
            (identity, identity, "invoice.posted", json.dumps({"invoice_id": invoice_id})),
        ),
    ]
    if body.fail_after is not None:
        statements.insert(body.fail_after, assertion(identity, "0"))
    try:
        return await commands.execute(
            identity=identity,
            request_hash=request_hash,
            statements=statements,
            response=response,
        )
    except CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        if "CHECK constraint failed" in str(exc) or "UNIQUE constraint failed" in str(exc):
            raise HTTPException(409, "Command rejected atomically") from exc
        raise


@router.get("/state/{stock_id}")
async def state(stock_id: str, env=Depends(environment)):
    db = D1Database(env.DB)
    current = await db.first("SELECT * FROM proof_stock WHERE id=?", (stock_id,))
    counts = {}
    for name in ["cf_commands", "proof_invoices", "proof_journal", "proof_audit", "cf_outbox"]:
        counts[name] = (await db.first(f"SELECT COUNT(*) AS n FROM {name}"))["n"]
    return {"stock": current, "counts": counts}


@router.post("/backup-restore")
async def backup_restore(env=Depends(environment)):
    from erp_cf.recovery_proof import round_trip

    try:
        return await round_trip(env, f"proof/{secrets.token_hex(16)}.json")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/password-kdf")
async def password_kdf(env=Depends(environment)):
    from erp_cf.crypto import (
        MEMORY_KIB,
        PARALLELISM,
        TIME_COST,
        b64,
        derive,
        hash_password,
        verify_password,
    )

    value = "Synthetic Workers password Δ"
    encoded = await hash_password(value)
    return {
        "algorithm": "Argon2id",
        "time_cost": TIME_COST,
        "memory_kib": MEMORY_KIB,
        "parallelism": PARALLELISM,
        "correct": await verify_password(encoded, value),
        "incorrect": await verify_password(encoded, "incorrect"),
        "vector": b64(await derive(value, bytes(range(16)))),
    }
