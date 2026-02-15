-- NAVIG Database Initialization
-- Runs on first PostgreSQL container start

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ── Memory / RAG table ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS navig_memories (
    id              BIGSERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    embedding       vector(1536),
    metadata        JSONB DEFAULT '{}',
    source          VARCHAR(255),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON navig_memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_memories_metadata
    ON navig_memories USING gin (metadata);
CREATE INDEX IF NOT EXISTS idx_memories_source
    ON navig_memories (source);

-- ── Session / state table ────────────────────────────────────
CREATE TABLE IF NOT EXISTS navig_sessions (
    id              BIGSERIAL PRIMARY KEY,
    session_id      VARCHAR(255) UNIQUE NOT NULL,
    agent_id        VARCHAR(255),
    state           JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Audit log ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS navig_audit (
    id              BIGSERIAL PRIMARY KEY,
    action          VARCHAR(255) NOT NULL,
    actor           VARCHAR(255),
    target          VARCHAR(255),
    details         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON navig_audit (action);
CREATE INDEX IF NOT EXISTS idx_audit_created
    ON navig_audit (created_at);

-- ── Knowledge base table ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS navig_knowledge (
    id              BIGSERIAL PRIMARY KEY,
    title           VARCHAR(512) NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(1536),
    category        VARCHAR(255),
    tags            TEXT[] DEFAULT '{}',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
    ON navig_knowledge USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_knowledge_category
    ON navig_knowledge (category);
CREATE INDEX IF NOT EXISTS idx_knowledge_tags
    ON navig_knowledge USING gin (tags);

-- ── Matrix rooms mirror (optional, synced from local SQLite) ─
CREATE TABLE IF NOT EXISTS navig_matrix_rooms (
    room_id         VARCHAR(255) PRIMARY KEY,
    alias           VARCHAR(255) DEFAULT '',
    name            VARCHAR(512) DEFAULT '',
    topic           TEXT DEFAULT '',
    purpose         VARCHAR(50) DEFAULT 'general',
    encrypted       BOOLEAN DEFAULT FALSE,
    joined_at       TIMESTAMPTZ DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'
);

-- ── Matrix events mirror (recent, pruned) ────────────────────
CREATE TABLE IF NOT EXISTS navig_matrix_events (
    event_id        VARCHAR(255) PRIMARY KEY,
    room_id         VARCHAR(255) NOT NULL REFERENCES navig_matrix_rooms(room_id) ON DELETE CASCADE,
    sender          VARCHAR(255) NOT NULL,
    event_type      VARCHAR(100) NOT NULL,
    content         JSONB DEFAULT '{}',
    origin_ts       BIGINT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_matrix_events_room_ts
    ON navig_matrix_events (room_id, origin_ts);
CREATE INDEX IF NOT EXISTS idx_matrix_events_sender
    ON navig_matrix_events (sender);
