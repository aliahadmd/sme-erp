-- Control metadata is deliberately excluded from captured row data.
CREATE TABLE platform_backup_runs (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 run_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('requested','capturing','publishing','complete','failed','deleting','deleted')),
 fence INTEGER,
 schema_json TEXT,
 schema_hash TEXT,
 manifest_hash TEXT,
 table_index INTEGER NOT NULL DEFAULT 0,
 cursor TEXT,
 page_index INTEGER NOT NULL DEFAULT 0,
 rows INTEGER NOT NULL DEFAULT 0,
 bytes INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 capture_started_at TEXT,
 updated_at TEXT NOT NULL,
 completed_at TEXT,
 last_error TEXT,
 UNIQUE(org_id,run_key),
 CHECK(version>=0 AND version<=9007199254740991)
) STRICT;
CREATE TABLE platform_backup_pages (
 backup_id TEXT NOT NULL REFERENCES platform_backup_runs(id),
 sequence INTEGER NOT NULL,
 table_name TEXT NOT NULL,
 object_key TEXT NOT NULL UNIQUE,
 sha256 TEXT NOT NULL,
 rows INTEGER NOT NULL,
 bytes INTEGER NOT NULL,
 first_cursor TEXT NOT NULL,
 last_cursor TEXT NOT NULL,
 PRIMARY KEY(backup_id,sequence)
) STRICT;
CREATE TABLE platform_restore_runs (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 backup_id TEXT NOT NULL REFERENCES platform_backup_runs(id),
 target_database_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('requested','restoring','complete','failed')),
 phase TEXT NOT NULL DEFAULT 'data',
 table_index INTEGER NOT NULL DEFAULT 0,
 page_index INTEGER NOT NULL DEFAULT 0,
 object_index INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 completed_at TEXT,
 result_json TEXT,
 last_error TEXT,
 CHECK(version>=0 AND version<=9007199254740991)
) STRICT;
CREATE TABLE platform_restore_receipts (
 restore_id TEXT NOT NULL,
 step_key TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(restore_id,step_key)
) STRICT;
CREATE TABLE platform_recovery_policy (
 singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),
 hold_external INTEGER NOT NULL CHECK(hold_external IN (0,1)),
 restored_from TEXT NOT NULL,
 restored_at TEXT NOT NULL
) STRICT;
CREATE INDEX platform_backup_pending ON platform_backup_runs(org_id,status,created_at);
CREATE INDEX platform_restore_pending ON platform_restore_runs(org_id,status,created_at);
