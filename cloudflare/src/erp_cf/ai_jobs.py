"""Model I/O happens outside the organization command lock; D1 owns the draft."""

import hashlib
import json
import uuid

from erp_cf.ai_client import DESCRIPTION_SYSTEM, AIBudgetExceeded, AIClient, description_prompt
from erp_cf.ai_commands import task_source
from erp_cf.background import JobFailed, system
from erp_cf.db import D1Database
from erp_cf.errors import DomainError
from erp_cf.store import UnitOfWork


async def process_ai_event(env, body, event):
    if event["completed_at"] or int(event["generation"]) != body.generation:
        return
    # This plan is used only for current primary reads/preflight. Its SQL guards
    # are rebuilt and committed by finish_task after inference completes.
    plan = UnitOfWork(D1Database(env.DB), str(env.ORG_ID), None, uuid.uuid4().hex, "ai.read", {})
    _, snapshot, _ = await task_source(plan, event)
    result = None
    if snapshot:
        try:
            generated = await AIClient(env).complete(
                system=DESCRIPTION_SYSTEM,
                prompt=description_prompt(snapshot),
            )
            result = {"text": generated.text, "model": generated.model}
        except AIBudgetExceeded as exc:
            raise JobFailed(exc.code, defer_until=exc.reset_at + 30000) from exc
        except DomainError as exc:
            raise JobFailed(exc.code) from exc
    digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:24]
    await system(
        env,
        "ai.task.finish",
        f"ai-finish:{body.event_id}:{body.generation}:{digest}",
        {"event_id": str(body.event_id), "generation": body.generation, "result": result},
    )
