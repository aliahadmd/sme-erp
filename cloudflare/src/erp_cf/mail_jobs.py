"""Delivery outside the business command lock, with a durable pre-send claim."""

import json
import logging
import uuid

from erp_cf.background import system
from erp_cf.mail_provider import send

logger = logging.getLogger("erp.mail")


async def process_mail_event(env, body):
    attempt = str(uuid.uuid4())
    payload = {"event_id": str(body.event_id), "generation": body.generation, "attempt_id": attempt}
    prepared = await system(env, "mail.prepare", "mail-prepare:" + attempt, payload)
    logger.info(
        json.dumps(
            {
                "event": "mail_prepared",
                "event_id": str(body.event_id),
                "action": prepared["action"],
                "status": prepared.get("status"),
            }
        )
    )
    if prepared["action"] != "send":
        return
    result = await send(env, prepared["message"])
    try:
        receipt = await system(
            env, "mail.finish", "mail-finish:" + attempt, {**payload, "result": result}
        )
        logger.info(
            json.dumps(
                {
                    "event": "mail_outcome",
                    "event_id": str(body.event_id),
                    "mail_id": prepared["mail_id"],
                    "attempt_id": attempt,
                    "outcome": receipt["outcome"],
                    "provider_id": result.get("provider_id"),
                }
            )
        )
    except Exception:
        # Provider IDs enable reconciliation without exposing recipients/body.
        logger.error(
            "mail_outcome_unrecorded mail_id=%s attempt_id=%s provider_id=%s",
            prepared["mail_id"],
            attempt,
            result.get("provider_id"),
        )
        raise
