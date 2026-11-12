-- Foundation shared by the feasibility proof and migrated business modules.
CREATE TABLE IF NOT EXISTS cf_commands (
    id TEXT PRIMARY KEY NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE IF NOT EXISTS cf_assertions (
    command_id TEXT NOT NULL,
    valid INTEGER NOT NULL CHECK (valid = 1)
) STRICT;

CREATE TABLE IF NOT EXISTS cf_outbox (
    id TEXT PRIMARY KEY NOT NULL,
    command_id TEXT NOT NULL REFERENCES cf_commands(id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    dispatched_at TEXT,
    completed_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS cf_outbox_pending ON cf_outbox(dispatched_at, created_at);
