"""The existing /api/auth contract backed by Workers crypto and D1 sessions."""

import hashlib
import secrets
import time
import uuid

from app.modules.core.schemas import LoginIn, MeOut, TokenOut
from fastapi import APIRouter, Depends, Request, Response

from erp_cf.crypto import (
    DUMMY_HASH,
    SESSION_SECONDS,
    create_access,
    token_hash,
    verify_password,
)
from erp_cf.db import D1Database, Statement, assertion
from erp_cf.errors import AuthenticationError, DomainError, PermissionDeniedError
from erp_cf.identity import Actor, current_actor, environment
from erp_cf.store import Repository, now, tables

router = APIRouter(prefix="/api/auth")
REFRESH_COOKIE = "refresh_token"


def same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = str(environment(request).PUBLIC_ORIGIN)
    if origin is not None and origin != expected:
        raise PermissionDeniedError("Request origin is not allowed")


def set_cookie(response: Response, token: str, expires: int):
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=max(0, expires - int(time.time())),
        path="/api/auth",
    )


def client_ip(request: Request):
    # Cloudflare supplies this header on the public Worker boundary.
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )


def login_audit(org_id, user_id, ip):
    return tables()["core_audit_logs"].insert(
        {
            "org_id": str(org_id),
            "user_id": user_id,
            "action": "login",
            "entity_type": "core.user",
            "entity_id": user_id,
            "ip": ip,
        }
    )[0]


async def commit_login(db, statements):
    try:
        await db.batch(statements)
    except Exception as exc:
        if "CHECK constraint failed" in str(exc):
            raise AuthenticationError("Account changed during sign-in; try again") from exc
        raise


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, response: Response):
    same_origin(request)
    env = environment(request)
    db = D1Database(env.DB)
    email = str(body.email).strip().lower()
    scope = hashlib.sha256(f"{client_ip(request)}\0{email}".encode()).hexdigest()
    guard = env.AUTH_LIMITERS.getByName(scope)
    permit = await guard.reserve()
    if permit is None:
        raise DomainError(
            "Too many failed login attempts — try again later", code="rate_limited", status=429
        )
    verified = False
    try:
        user = await Repository(db, "core_users").first("email=?", (email,))
        matches = await verify_password(
            user["hashed_password"] if user else DUMMY_HASH, body.password
        )
        if not user or not matches:
            raise AuthenticationError("Invalid email or password")
        if not user["is_active"]:
            raise AuthenticationError("This account is disabled")
        verified = True
        timestamp = int(time.time())
        expires = timestamp + SESSION_SECONDS
        session = str(uuid.uuid7())
        refresh = secrets.token_urlsafe(48)
        identity = "login-" + session
        await commit_login(
            db,
            [
                assertion(
                    identity,
                    "EXISTS(SELECT 1 FROM core_users WHERE id=? AND is_active=1 "
                    "AND hashed_password=?)",
                    (user["id"], user["hashed_password"]),
                ),
                Statement(
                    "INSERT INTO platform_sessions (id,user_id,created_at,expires_at) "
                    "VALUES (?,?,?,?)",
                    (session, user["id"], timestamp, expires),
                ),
                Statement(
                    "INSERT INTO platform_refresh_tokens "
                    "(token_hash,session_id,created_at,expires_at) VALUES (?,?,?,?)",
                    (token_hash(refresh), session, timestamp, expires),
                ),
                Statement(
                    "UPDATE core_users SET last_login_at=?,updated_at=?,_revision=_revision+1 "
                    "WHERE id=?",
                    (now().isoformat(), now().isoformat(), user["id"]),
                ),
                login_audit(env.ORG_ID, user["id"], client_ip(request)),
                Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)),
            ],
        )
        access = create_access(env, user["id"], session)
        set_cookie(response, refresh, expires)
        return TokenOut(access_token=access)
    finally:
        await guard.finish(permit, verified)


async def refresh_state(db, hashed):
    return await db.first(
        "SELECT t.token_hash,t.session_id,CAST(t.used_at AS TEXT) AS used_at,"
        "CAST(t.expires_at AS TEXT) AS token_expires,CAST(s.expires_at AS TEXT) AS session_expires,"
        "CAST(s.revoked_at AS TEXT) AS revoked_at,s.user_id,CAST(u.is_active AS TEXT) AS active "
        "FROM platform_refresh_tokens t JOIN platform_sessions s ON s.id=t.session_id "
        "JOIN core_users u ON u.id=s.user_id WHERE t.token_hash=?",
        (hashed,),
    )


async def revoke(db, session: str):
    await db.batch(
        [
            Statement(
                "UPDATE platform_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (int(time.time()), session),
            )
        ]
    )


@router.post("/refresh", response_model=TokenOut)
async def refresh(request: Request, response: Response):
    same_origin(request)
    env = environment(request)
    db = D1Database(env.DB)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token or len(token) > 128:
        raise AuthenticationError("No valid refresh token")
    hashed = token_hash(token)
    state = await refresh_state(db, hashed)
    timestamp = int(time.time())
    if state is None:
        raise AuthenticationError("Refresh token was revoked")
    if state["used_at"] is not None:
        await revoke(db, state["session_id"])
        raise AuthenticationError("Refresh token reuse detected; sign in again")
    if (
        state["revoked_at"] is not None
        or state["active"] != "1"
        or min(int(state["token_expires"]), int(state["session_expires"])) <= timestamp
    ):
        raise AuthenticationError("Session expired or was revoked")
    new_refresh = secrets.token_urlsafe(48)
    identity = "refresh-" + secrets.token_hex(16)
    try:
        await db.batch(
            [
                assertion(
                    identity,
                    "EXISTS(SELECT 1 FROM platform_refresh_tokens t "
                    "JOIN platform_sessions s ON s.id=t.session_id "
                    "JOIN core_users u ON u.id=s.user_id "
                    "WHERE t.token_hash=? AND t.used_at IS NULL AND t.expires_at>? "
                    "AND s.revoked_at IS NULL AND s.expires_at>? AND u.is_active=1)",
                    (hashed, timestamp, timestamp),
                ),
                Statement(
                    "UPDATE platform_refresh_tokens SET used_at=? WHERE token_hash=?",
                    (timestamp, hashed),
                ),
                Statement(
                    "INSERT INTO platform_refresh_tokens "
                    "(token_hash,session_id,created_at,expires_at) VALUES (?,?,?,?)",
                    (
                        token_hash(new_refresh),
                        state["session_id"],
                        timestamp,
                        int(state["session_expires"]),
                    ),
                ),
                Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)),
            ]
        )
    except Exception:
        # A concurrent request may have won. Confirm consumption before revoking
        # the family; an unrelated D1 outage is not evidence of token reuse.
        current = await refresh_state(db, hashed)
        if current and current["used_at"] is not None:
            await revoke(db, current["session_id"])
            raise AuthenticationError("Refresh token reuse detected; sign in again") from None
        if (
            current is None
            or current["revoked_at"] is not None
            or current["active"] != "1"
            or min(int(current["token_expires"]), int(current["session_expires"]))
            <= int(time.time())
        ):
            raise AuthenticationError("Session expired or was revoked") from None
        raise
    set_cookie(response, new_refresh, int(state["session_expires"]))
    return TokenOut(access_token=create_access(env, state["user_id"], state["session_id"]))


@router.post("/logout")
async def logout(request: Request, response: Response):
    same_origin(request)
    db = D1Database(environment(request).DB)
    token = request.cookies.get(REFRESH_COOKIE)
    if token and len(token) <= 128:
        state = await refresh_state(db, token_hash(token))
        if state:
            await revoke(db, state["session_id"])
    response.delete_cookie(
        REFRESH_COOKIE, path="/api/auth", secure=True, httponly=True, samesite="lax"
    )
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
async def me(actor: Actor = Depends(current_actor)):
    return {
        **actor.user,
        "roles": [r["code"] for r in actor.roles],
        "permissions": sorted(actor.permissions),
    }
