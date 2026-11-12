-- Authoritative sessions and one-time refresh credentials. Never use KV for revocation.
CREATE TABLE platform_sessions (
    id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES core_users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    CHECK (expires_at > created_at)
) STRICT;
CREATE INDEX platform_sessions_user ON platform_sessions(user_id);
CREATE INDEX platform_sessions_expiry ON platform_sessions(expires_at);

CREATE TABLE platform_refresh_tokens (
    token_hash TEXT PRIMARY KEY NOT NULL CHECK (length(token_hash)=64),
    session_id TEXT NOT NULL REFERENCES platform_sessions(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    CHECK (expires_at > created_at)
) STRICT;
CREATE INDEX platform_refresh_session ON platform_refresh_tokens(session_id);
CREATE INDEX platform_refresh_expiry ON platform_refresh_tokens(expires_at);
