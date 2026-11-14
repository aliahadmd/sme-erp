"""Temporary, administrator-only restart verification on the isolated proof Worker."""

from contextlib import suppress

from fastapi import APIRouter, Request

from erp_cf.db import plain
from erp_cf.errors import NotFoundError, PermissionDeniedError
from erp_cf.identity import bearer, environment, from_token

ORIGIN = "https://erp-migration-staging-20261113.aliahad.workers.dev"
router = APIRouter(prefix="/internal/proofs/coordinator", include_in_schema=False)


def enabled(env):
    if (
        str(env.ENVIRONMENT) != "staging"
        or str(getattr(env, "PUBLIC_ORIGIN", "")) != ORIGIN
        or str(getattr(env, "RESTART_PROOFS_ENABLED", "false")) != "true"
    ):
        raise NotFoundError()


async def administrator(env, token):
    enabled(env)
    actor = await from_token(env, token)
    if not actor.is_superuser:
        raise PermissionDeniedError("Only the isolated staging administrator may run this proof")
    return actor


def stub(env):
    options = {"retry": {"maxRetries": 0}}
    try:
        from js import JSON
    except ImportError:
        pass
    else:
        options = JSON.parse('{"retry":{"maxRetries":0}}')
    return env.COMMANDS.getByName(str(env.ORG_ID), options)


@router.get("/instance")
async def instance(request: Request):
    env = environment(request)
    enabled(env)
    token = bearer(request)
    await administrator(env, token)
    return plain(await stub(env).inspect_instance({"token": token}))


@router.post("/restart", status_code=202)
async def restart(request: Request):
    env = environment(request)
    enabled(env)
    token = bearer(request)
    await administrator(env, token)
    # ctx.abort() is expected to break this RPC. A changed instance ID,
    # checked separately by the verifier, is the proof of an actual reset.
    with suppress(Exception):
        await stub(env).restart_instance({"token": token})
    return {"status": "reset_requested"}
