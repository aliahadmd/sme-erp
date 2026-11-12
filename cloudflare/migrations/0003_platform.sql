-- Migration receipts are separate from the original ERP's business models.
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    name TEXT PRIMARY KEY NOT NULL,
    sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
