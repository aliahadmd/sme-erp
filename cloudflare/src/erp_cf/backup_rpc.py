"""Explicit error envelopes preserve HTTP contracts across native DO RPC."""

import logging

from erp_cf.db import plain
from erp_cf.errors import DomainError


async def respond(callback, *args, **kwargs):
    try:
        return {"status": 200, "body": await callback(*args, **kwargs)}
    except DomainError as exc:
        return {"status": exc.status, "body": exc.response()}
    except Exception as exc:
        logging.getLogger("erp.backups").error(
            "backup_rpc_failed error_type=%s", type(exc).__name__
        )
        return {
            "status": 503,
            "body": {
                "error": {"code": "backup_unavailable", "detail": "Backup service unavailable"}
            },
        }


async def call(stub, method, *args):
    response = plain(await getattr(stub, method)(*args))
    if response["status"] != 200:
        error = response["body"]["error"]
        raise DomainError(error["detail"], code=error["code"], status=response["status"])
    return response["body"]
