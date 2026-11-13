"""Atomic AI review, bounded batch planning and idempotent task completion."""

import json
import uuid

from pydantic import BaseModel, Field

from erp_cf.db import Statement, assertion
from erp_cf.errors import ConflictError, NotFoundError
from erp_cf.store import now, wire

ELIGIBLE = (
    "status='active' AND (description IS NULL OR description='') "
    "AND NOT EXISTS(SELECT 1 FROM core_ai_drafts d WHERE d.org_id=catalog_products.org_id "
    "AND d.entity_id=catalog_products.id AND d.entity_type='product' "
    "AND d.field='description' AND d.status='pending') "
    "AND NOT EXISTS(SELECT 1 FROM cf_ai_tasks t WHERE t.org_id=catalog_products.org_id "
    "AND t.product_id=catalog_products.id AND t.status='pending')"
)


def description_snapshot(product, category):
    return {
        "product_revision": product["_revision"],
        "name": product["name"],
        "type": product["type"],
        "category_id": product["category_id"],
        "category_revision": category["_revision"] if category else None,
        "category": category["name"] if category else "uncategorized",
    }


async def request_bulk(plan, actor, payload):
    row = await plan.db.first(
        "SELECT CAST(COUNT(*) AS TEXT) AS n,MAX(id) AS high_water "
        "FROM catalog_products WHERE org_id=? AND " + ELIGIBLE,
        (plan.org_id,),
    )
    count = int(row["n"])
    if count:
        identity = str(uuid.uuid7())
        plan.statements.append(
            Statement(
                "INSERT INTO cf_ai_batches(id,org_id,actor_id,high_water,queued,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (identity, plan.org_id, actor.id, row["high_water"], count, now().isoformat()),
            )
        )
        plan.emit("ai.batch.requested", {"batch_id": identity})
    plan.audit("request", "ai.bulk_descriptions", after={"queued": count})
    return {"queued": count}


async def batch_page(plan, env, payload):
    batch = await plan.db.first(
        "SELECT * FROM cf_ai_batches WHERE id=? AND org_id=?", (payload["batch_id"], plan.org_id)
    )
    if not batch:
        raise NotFoundError("AI batch not found")
    permitted = await plan.db.first("SELECT " + REQUESTER + " AS permitted", (batch["actor_id"],))
    if not permitted["permitted"]:
        plan.statements.append(
            Statement(
                "UPDATE cf_ai_batches SET cursor=high_water WHERE id=?",
                (batch["id"],),
            )
        )
        return "not_authorized"
    rows = await plan.repository("catalog_products").find(
        "id>? AND id<=? AND " + ELIGIBLE,
        (batch["cursor"], batch["high_water"]),
        order_by="id",
        limit=25,
    )
    categories = await plan.repository("catalog_product_categories").many(
        [p["category_id"] for p in rows if p["category_id"]],
        required=False,
    )
    for product in rows:
        plan.guard("catalog_products", product)
        category = categories.get(product["category_id"])
        if category:
            plan.guard("catalog_product_categories", category)
        identity = str(uuid.uuid7())
        plan.statements.append(
            Statement(
                "INSERT INTO cf_ai_tasks(id,org_id,batch_id,product_id,snapshot_json) "
                "VALUES (?,?,?,?,?)",
                (
                    identity,
                    plan.org_id,
                    batch["id"],
                    product["id"],
                    json.dumps(wire(description_snapshot(product, category)), ensure_ascii=False),
                ),
            )
        )
        plan.emit("ai.product.requested", {"task_id": identity})
    cursor = rows[-1]["id"] if rows else batch["high_water"]
    plan.statements.extend(
        [
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM cf_ai_batches WHERE id=? AND cursor=?)",
                (batch["id"], batch["cursor"]),
            ),
            Statement(
                "UPDATE cf_ai_batches SET cursor=?,dispatched=dispatched+? WHERE id=?",
                (cursor, len(rows), batch["id"]),
            ),
        ]
    )
    if len(rows) == 25 and cursor < batch["high_water"]:
        plan.emit("ai.batch.requested", {"batch_id": batch["id"]})
    return "planned"


async def review(plan, actor, payload, *, accept):
    draft = await plan.repository("core_ai_drafts").get(payload["id"])
    if draft["status"] != "pending":
        raise ConflictError(f"Draft is already {draft['status']}")
    if accept:
        if draft["entity_type"] != "product" or draft["field"] != "description":
            raise NotFoundError("Unsupported draft entity or field")
        product = await plan.repository("catalog_products").get(draft["entity_id"])
        plan.update("catalog_products", product, {"description": draft["draft_text"]})
    status = "accepted" if accept else "discarded"
    plan.update("core_ai_drafts", draft, {"status": status})
    plan.audit("accept" if accept else "discard", "ai.draft", draft["id"])
    return {"status": status}


async def accept(plan, actor, payload):
    return await review(plan, actor, payload, accept=True)


async def discard(plan, actor, payload):
    return await review(plan, actor, payload, accept=False)


REQUESTER = (
    "EXISTS(SELECT 1 FROM core_users u WHERE u.id=? AND u.is_active=1 AND "
    "(u.is_superuser=1 OR EXISTS(SELECT 1 FROM core_user_roles ur "
    "JOIN core_role_permissions rp ON rp.role_id=ur.role_id "
    "JOIN core_permissions p ON p.id=rp.permission_id "
    "WHERE ur.user_id=u.id AND p.code='catalog.product.update')))"
)


async def task_source(plan, event):
    data = json.loads(event["payload_json"])
    task = await plan.db.first(
        "SELECT t.id,t.product_id,t.snapshot_json,t.status,b.actor_id "
        "FROM cf_ai_tasks t JOIN cf_ai_batches b ON b.id=t.batch_id "
        "WHERE t.id=? AND t.org_id=?",
        (data["task_id"], plan.org_id),
    )
    if not task:
        raise NotFoundError("AI task not found")
    if task["status"] != "pending":
        return task, None, "already_processed"
    permitted = await plan.db.first("SELECT " + REQUESTER + " AS permitted", (task["actor_id"],))
    if not permitted["permitted"]:
        return task, None, "not_authorized"
    product = await plan.repository("catalog_products").get(task["product_id"], required=False)
    if not product or product["status"] != "active" or product["description"]:
        return task, None, "stale_source"
    category = (
        await plan.repository("catalog_product_categories").get(
            product["category_id"],
            required=False,
        )
        if product["category_id"]
        else None
    )
    snapshot = json.loads(task["snapshot_json"])
    if wire(description_snapshot(product, category)) != snapshot:
        return task, None, "stale_source"
    pending = await plan.repository("core_ai_drafts").first(
        "entity_type='product' AND entity_id=? AND field='description' AND status='pending'",
        (product["id"],),
    )
    if pending:
        return task, None, "already_drafted"
    plan.guard("catalog_products", product)
    if category:
        plan.guard("catalog_product_categories", category)
    plan.statements.append(assertion(plan.identity, REQUESTER, (task["actor_id"],)))
    return task, snapshot, None


class Completion(BaseModel):
    text: str = Field(min_length=1, max_length=8192)
    model: str = Field(min_length=1, max_length=100, pattern=r"^@cf/")


async def finish_task(plan, env, payload):
    from erp_cf.job_commands import complete_event

    event = await plan.db.first(
        "SELECT id,event_type,payload_json,completed_at,"
        "CAST(delivery_generation AS TEXT) AS generation FROM cf_outbox "
        "WHERE id=? AND json_extract(payload_json,'$.org_id')=?",
        (payload["event_id"], plan.org_id),
    )
    if not event or event["event_type"] != "ai.product.requested":
        raise NotFoundError("AI event not found")
    if event["completed_at"] or int(event["generation"]) != payload["generation"]:
        return {"outcome": "already_processed"}
    task, snapshot, skip = await task_source(plan, event)
    draft_id = None
    if snapshot:
        if not payload.get("result"):
            raise ConflictError("AI source became eligible again")
        result = Completion.model_validate(payload["result"])
        draft = plan.add(
            "core_ai_drafts",
            {
                "entity_type": "product",
                "entity_id": task["product_id"],
                "field": "description",
                "draft_text": result.text,
                "model": result.model,
            },
        )
        draft_id = draft["id"]
    timestamp = now().isoformat()
    plan.statements.extend(
        [
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM cf_ai_tasks WHERE id=? AND status='pending')",
                (task["id"],),
            ),
            Statement(
                "UPDATE cf_ai_tasks SET status=?,draft_id=?,completed_at=? WHERE id=?",
                ("complete" if draft_id else "skipped", draft_id, timestamp, task["id"]),
            ),
        ]
    )
    outcome = "drafted" if draft_id else skip
    complete_event(plan, event, outcome)
    return {"outcome": outcome}


COMMANDS = {
    "ai.bulk.request": ("catalog.product.update", 200, request_bulk),
    "ai.draft.accept": ("catalog.product.update", 200, accept),
    "ai.draft.discard": ("catalog.product.update", 200, discard),
}
