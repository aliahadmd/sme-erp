ALTER TABLE cf_outbox ADD COLUMN available_at TEXT;
ALTER TABLE catalog_products ADD COLUMN _description_fold TEXT NOT NULL DEFAULT '';
UPDATE catalog_products SET _description_fold=lower(coalesce(description,''));
CREATE UNIQUE INDEX core_ai_one_pending_description ON core_ai_drafts
 (org_id,entity_type,entity_id,field)
 WHERE status='pending' AND entity_type='product' AND field='description';
CREATE TABLE cf_ai_batches (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 actor_id TEXT NOT NULL REFERENCES core_users(id),
 high_water TEXT NOT NULL,
 cursor TEXT NOT NULL DEFAULT '',
 queued INTEGER NOT NULL,
 dispatched INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE cf_ai_tasks (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES cf_ai_batches(id),
 product_id TEXT NOT NULL,
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','complete','skipped')),
 draft_id TEXT REFERENCES core_ai_drafts(id),
 completed_at TEXT
) STRICT;
CREATE UNIQUE INDEX cf_ai_one_pending_task ON cf_ai_tasks(org_id,product_id)
 WHERE status='pending';
CREATE INDEX cf_ai_batch_tasks ON cf_ai_tasks(batch_id,status);
