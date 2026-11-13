ALTER TABLE cf_outbox ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'pending'
 CHECK(delivery_state IN ('pending','queued','complete','dead'));
ALTER TABLE cf_outbox ADD COLUMN dispatch_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cf_outbox ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cf_outbox ADD COLUMN last_error TEXT;
ALTER TABLE cf_outbox ADD COLUMN dead_at TEXT;
ALTER TABLE cf_outbox ADD COLUMN delivery_generation INTEGER NOT NULL DEFAULT 0
 CHECK(delivery_generation>=0 AND delivery_generation<=9007199254740991);
CREATE INDEX cf_outbox_org_delivery ON cf_outbox(
 json_extract(payload_json,'$.org_id'),completed_at,delivery_state,dispatched_at
);
CREATE TABLE cf_event_receipts (
 event_id TEXT PRIMARY KEY NOT NULL REFERENCES cf_outbox(id),
 org_id TEXT NOT NULL,
 outcome TEXT NOT NULL,
 processed_at TEXT NOT NULL
) STRICT;
CREATE TABLE cf_notification_keys (
 org_id TEXT NOT NULL,
 user_id TEXT NOT NULL REFERENCES core_users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL,
 dedupe_key TEXT NOT NULL,
 notification_id TEXT NOT NULL REFERENCES core_notifications(id) ON DELETE CASCADE,
 PRIMARY KEY(org_id,user_id,kind,dedupe_key)
) STRICT;
CREATE TABLE cf_mail_outbox (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 event_id TEXT REFERENCES cf_outbox(id),
 recipient TEXT NOT NULL,
 user_id TEXT REFERENCES core_users(id) ON DELETE SET NULL,
 preference TEXT,
 subject TEXT NOT NULL,
 body TEXT NOT NULL,
 kind TEXT NOT NULL,
 source_id TEXT,
 status TEXT NOT NULL CHECK(status IN ('disabled','test','pending','sent','uncertain','dead','cancelled')),
 provider_id TEXT,
 last_error TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT
) STRICT;
CREATE INDEX cf_mail_org_status ON cf_mail_outbox(org_id,status,created_at);
CREATE TABLE cf_dead_letters (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 event_id TEXT REFERENCES cf_outbox(id),
 provider_message_id TEXT NOT NULL,
 delivery_generation INTEGER NOT NULL,
 body_hash TEXT NOT NULL,
 reason TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('quarantined','replayed','resolved')),
 created_at TEXT NOT NULL,
 last_seen_at TEXT NOT NULL,
 replayed_at TEXT
) STRICT;
CREATE INDEX cf_dead_org_state ON cf_dead_letters(org_id,state,created_at);
CREATE TABLE cf_job_runs (
 org_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('overdue','quotes','audit')),
 run_key TEXT NOT NULL,
 on_date TEXT NOT NULL,
 cursor TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('running','complete')),
 version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0 AND version<=9007199254740991),
 processed INTEGER NOT NULL DEFAULT 0,
 started_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 finished_at TEXT,
 PRIMARY KEY(org_id,kind,run_key)
) STRICT;
CREATE TABLE cf_scheduler_state (
 org_id TEXT PRIMARY KEY NOT NULL,
 last_trigger_at TEXT NOT NULL,
 last_success_at TEXT,
 last_error TEXT
) STRICT;
