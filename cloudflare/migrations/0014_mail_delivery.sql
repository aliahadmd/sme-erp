ALTER TABLE cf_mail_outbox ADD COLUMN delivery_event_id TEXT REFERENCES cf_outbox(id);
ALTER TABLE cf_mail_outbox ADD COLUMN attempt_id TEXT;
ALTER TABLE cf_mail_outbox ADD COLUMN claim_until TEXT;
ALTER TABLE cf_mail_outbox ADD COLUMN version INTEGER NOT NULL DEFAULT 0
 CHECK(version>=0 AND version<=9007199254740991);
UPDATE cf_mail_outbox SET delivery_event_id=(
 SELECT o.id FROM cf_outbox o WHERE o.event_type='mail.delivery.requested'
 AND json_extract(o.payload_json,'$.mail_id')=cf_mail_outbox.id
 AND json_extract(o.payload_json,'$.org_id')=cf_mail_outbox.org_id
 ORDER BY o.created_at DESC,o.id DESC LIMIT 1
) WHERE status='pending';
CREATE TABLE cf_mail_attempts (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 mail_id TEXT NOT NULL REFERENCES cf_mail_outbox(id),
 event_id TEXT NOT NULL REFERENCES cf_outbox(id),
 generation INTEGER NOT NULL CHECK(generation>=0 AND generation<=9007199254740991),
 state TEXT NOT NULL CHECK(state IN ('prepared','accepted','rejected','uncertain','test')),
 payload_hash TEXT NOT NULL,
 provider_id TEXT,
 error_code TEXT,
 created_at TEXT NOT NULL,
 finished_at TEXT
) STRICT;
CREATE INDEX cf_mail_attempts_mail ON cf_mail_attempts(org_id,mail_id,created_at);
