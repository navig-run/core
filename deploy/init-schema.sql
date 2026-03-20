-- schema.sql — NAVIG telemetry database schema
-- Usage: sqlite3 installs.db < schema.sql

CREATE TABLE IF NOT EXISTS installs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    anon_id_hash   TEXT    UNIQUE NOT NULL,
    platform       TEXT    NOT NULL DEFAULT 'unknown',
    arch           TEXT    NOT NULL DEFAULT 'unknown',
    python         TEXT    NOT NULL DEFAULT 'unknown',
    first_seen     TEXT    NOT NULL,
    last_seen      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_installs_platform ON installs (platform);
CREATE INDEX IF NOT EXISTS idx_installs_first_seen ON installs (first_seen);
