"""
Key Facts Store — Persistent conversational memory for NAVIG.

Stores distilled, structured facts extracted from conversations:
  - User preferences, explicit decisions, recurring topics
  - Each fact: content, source, timestamp, tags, confidence, embedding
  - CRUD with soft-delete (facts are correctable as context evolves)
  - SQLite with WAL + FTS5 for keyword search + optional vector search

This is the "ChatGPT memory" equivalent for NAVIG: raw conversation
history is never injected wholesale — only ranked key facts.

Storage: ~/.navig/memory/key_facts.db
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.memory.paths import get_key_facts_db_path

logger = logging.getLogger("navig.memory.key_facts")


# ── Helpers ───────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ts() -> datetime:
    return datetime.now(timezone.utc)


from navig.core.tokens import estimate_tokens as _estimate_tokens

# ── Data Model ────────────────────────────────────────────────


@dataclass
class KeyFact:
    """
    A single distilled fact from a conversation.

    Fields:
        id:              UUID primary key
        content:         The fact text (e.g., "User prefers Python 3.12+")
        category:        Classification tag (preference, decision, context, identity, technical)
        tags:            Free-form topic tags for filtering
        confidence:      0.0–1.0 extraction confidence / relevance score
        source_conversation_id:  Session key where fact was extracted
        source_platform:         Platform of origin (bridge, gistium, telegram, core)
        created_at:      First extraction timestamp
        updated_at:      Last update timestamp
        superseded_by:   ID of the fact that replaces this one (soft-delete chain)
        deleted:         Soft-delete flag
        access_count:    Number of times fact was retrieved for context injection
        last_accessed:   Last retrieval timestamp
        embedding:       Optional vector embedding for semantic search
        metadata:        Arbitrary JSON metadata
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    category: str = "context"  # preference, decision, context, identity, technical
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    source_conversation_id: str = ""
    source_platform: str = "core"
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    superseded_by: str | None = None
    deleted: bool = False
    access_count: int = 0
    last_accessed: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Curation status: 1 = approved (used in retrieval), None = pending (proposed
    # by the agent, awaiting review), 0 = rejected. Defaults to approved so
    # directly-constructed / manual facts work unchanged; proposers set None.
    approved: int | None = 1

    @property
    def token_count(self) -> int:
        return _estimate_tokens(self.content)

    @property
    def is_active(self) -> bool:
        return not self.deleted and self.superseded_by is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "tags": json.dumps(self.tags),
            "confidence": self.confidence,
            "source_conversation_id": self.source_conversation_id,
            "source_platform": self.source_platform,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "superseded_by": self.superseded_by,
            "deleted": int(self.deleted),
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "embedding": json.dumps(self.embedding) if self.embedding else None,
            "metadata": json.dumps(self.metadata),
            "approved": self.approved,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> KeyFact:
        return cls(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            confidence=row["confidence"],
            source_conversation_id=row["source_conversation_id"],
            source_platform=row["source_platform"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            superseded_by=row["superseded_by"],
            deleted=bool(row["deleted"]),
            access_count=row["access_count"],
            last_accessed=row["last_accessed"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            # Guard against a row from a pre-migration db (column absent → approved).
            approved=(row["approved"] if "approved" in row.keys() else 1),
        )

    def __repr__(self) -> str:
        status = "active" if self.is_active else ("superseded" if self.superseded_by else "deleted")
        return f"<KeyFact [{status}] {self.content[:60]}…>"


# ── Valid categories ──────────────────────────────────────────

VALID_CATEGORIES = {
    "preference",
    "decision",
    "context",
    "identity",
    "technical",
    "problem_solution",
}


# ── Key Facts Store ───────────────────────────────────────────


class KeyFactStore:
    """
    SQLite-backed store for persistent key facts (conversational memory).

    Thread-safe.  Uses WAL mode for concurrent reads.
    FTS5 index for fast keyword retrieval.
    Optional vector embeddings for semantic search.

    Usage:
        store = KeyFactStore()
        store.upsert(KeyFact(content="User prefers dark themes", category="preference"))
        facts = store.search("theme preference", limit=5)
        store.soft_delete(fact_id)
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        db_path: Path | None = None,
        embedding_provider: Any | None = None,
    ):
        self.db_path = db_path or get_key_facts_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = embedding_provider
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA cache_size=-8000")
        return self._local.conn

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS key_facts (
                id                      TEXT PRIMARY KEY,
                content                 TEXT NOT NULL,
                category                TEXT NOT NULL DEFAULT 'context',
                tags                    TEXT DEFAULT '[]',
                confidence              REAL DEFAULT 0.8,
                source_conversation_id  TEXT DEFAULT '',
                source_platform         TEXT DEFAULT 'core',
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL,
                superseded_by           TEXT,
                deleted                 INTEGER DEFAULT 0,
                access_count            INTEGER DEFAULT 0,
                last_accessed           TEXT,
                embedding               TEXT,
                metadata                TEXT DEFAULT '{}',
                approved                INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_kf_category ON key_facts(category);
            CREATE INDEX IF NOT EXISTS idx_kf_deleted ON key_facts(deleted);
            CREATE INDEX IF NOT EXISTS idx_kf_superseded ON key_facts(superseded_by);
            CREATE INDEX IF NOT EXISTS idx_kf_confidence ON key_facts(confidence DESC);
            CREATE INDEX IF NOT EXISTS idx_kf_updated ON key_facts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_kf_source ON key_facts(source_conversation_id);
        """
        )

        # Migration: add `approved` to pre-existing dbs (CREATE TABLE IF NOT EXISTS
        # won't alter them). Idempotent — guarded by a column-presence check. Backfill
        # existing rows to approved=1 so current memory keeps working; only NEW facts
        # proposed by the agent are inserted as pending (approved=NULL). The approved
        # index is created *after* this so it never references a not-yet-added column.
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(key_facts)")}
            if "approved" not in cols:
                conn.execute("ALTER TABLE key_facts ADD COLUMN approved INTEGER")
                conn.execute("UPDATE key_facts SET approved = 1 WHERE approved IS NULL")
                logger.info("key_facts: migrated existing rows to approved=1")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kf_approved ON key_facts(approved)")
        except sqlite3.OperationalError as exc:
            logger.warning("key_facts approved-column migration skipped: %s", exc)

        # FTS5 for full-text keyword search (standalone, not external content)
        self._fts_available = False
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS key_facts_fts USING fts5(
                    fact_id,
                    content,
                    tags,
                    category
                )
            """
            )
            self._fts_available = True
        except sqlite3.OperationalError:
            logger.warning("FTS5 not available — keyword search will use LIKE fallback")

        # Schema version tracking
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kf_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """
        )
        conn.execute(
            "INSERT OR IGNORE INTO kf_meta (key, value) VALUES ('schema_version', ?)",
            (str(self.SCHEMA_VERSION),),
        )
        conn.commit()
        logger.debug("KeyFactStore schema initialized at %s", self.db_path)

    # ── CRUD ──────────────────────────────────────────────────

    def upsert(self, fact: KeyFact) -> KeyFact:
        """
        Insert or update a key fact.  If a fact with the same content
        already exists (active, not deleted), update confidence + metadata
        instead of duplicating.
        """
        if fact.category not in VALID_CATEGORIES:
            fact.category = "context"

        # Embed if provider available and no embedding yet
        if self.embedding_provider and fact.embedding is None:
            try:
                fact.embedding = self.embedding_provider.embed_text(fact.content)
            except Exception as exc:
                logger.debug("Embedding failed for fact: %s", exc)

        conn = self._conn()
        with self._write_lock:
            # Check for near-duplicate by content
            existing = self._find_duplicate(fact.content)
            if existing and existing.id != fact.id:
                # Merge: bump confidence, update metadata, keep original ID
                existing.confidence = min(1.0, max(existing.confidence, fact.confidence) + 0.05)
                existing.updated_at = _utcnow()
                existing.metadata.update(fact.metadata)
                if fact.tags:
                    existing.tags = list(set(existing.tags + fact.tags))
                existing.embedding = fact.embedding or existing.embedding
                self._update_row(existing)
                return existing

            fact.updated_at = _utcnow()
            d = fact.to_dict()
            conn.execute(
                """
                INSERT OR REPLACE INTO key_facts
                (id, content, category, tags, confidence,
                 source_conversation_id, source_platform,
                 created_at, updated_at, superseded_by, deleted,
                 access_count, last_accessed, embedding, metadata, approved)
                VALUES
                (:id, :content, :category, :tags, :confidence,
                 :source_conversation_id, :source_platform,
                 :created_at, :updated_at, :superseded_by, :deleted,
                 :access_count, :last_accessed, :embedding, :metadata, :approved)
            """,
                d,
            )

            # Update FTS inside the same transaction for atomicity.
            self._update_fts_conn(conn, fact)
            conn.commit()

        return fact

    def get(self, fact_id: str) -> KeyFact | None:
        """Get a single fact by ID."""
        row = self._conn().execute("SELECT * FROM key_facts WHERE id = ?", (fact_id,)).fetchone()
        return KeyFact.from_row(row) if row else None

    def get_active(
        self,
        limit: int = 100,
        category: str | None = None,
        min_confidence: float = 0.0,
        approved: int | None = None,
    ) -> list[KeyFact]:
        """Get all active (non-deleted, non-superseded) facts.

        *approved*: when ``1`` returns only approved facts (the curation-gated set
        used for retrieval); when ``None`` (default) applies no approval filter.
        """
        query = "SELECT * FROM key_facts WHERE deleted = 0 AND superseded_by IS NULL"
        params: list[Any] = []

        if category:
            query += " AND category = ?"
            params.append(category)
        if min_confidence > 0:
            query += " AND confidence >= ?"
            params.append(min_confidence)
        if approved is not None:
            query += " AND approved = ?"
            params.append(approved)

        query += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn().execute(query, params).fetchall()
        return [KeyFact.from_row(r) for r in rows]

    # ── Curation (propose → approve / reject) ─────────────────

    def get_pending(self, limit: int = 100) -> list[KeyFact]:
        """Return facts the agent proposed that are awaiting review (approved IS NULL)."""
        rows = self._conn().execute(
            """SELECT * FROM key_facts
               WHERE deleted = 0 AND superseded_by IS NULL AND approved IS NULL
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [KeyFact.from_row(r) for r in rows]

    def set_approval(
        self, fact_id: str, approved: int | None, reason: str | None = None
    ) -> bool:
        """Set a fact's curation status (1=approved, 0=rejected, None=pending).

        On rejection, records *reason* under ``metadata["rejection_reason"]`` for audit.
        """
        with self._write_lock:
            conn = self._conn()
            fact = self.get(fact_id)
            if fact is None:
                return False
            meta = dict(fact.metadata)
            if approved == 0 and reason:
                meta["rejection_reason"] = reason
            cursor = conn.execute(
                "UPDATE key_facts SET approved = ?, metadata = ?, updated_at = ? WHERE id = ?",
                (approved, json.dumps(meta), _utcnow(), fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def approve(self, fact_id: str) -> bool:
        """Approve a proposed fact so retrieval may use it."""
        return self.set_approval(fact_id, 1)

    def reject(self, fact_id: str, reason: str | None = None) -> bool:
        """Reject a proposed fact (kept for audit, never injected)."""
        return self.set_approval(fact_id, 0, reason=reason)

    def approve_all_pending(self) -> int:
        """Approve every pending fact. Returns the number approved."""
        with self._write_lock:
            conn = self._conn()
            cursor = conn.execute(
                "UPDATE key_facts SET approved = 1, updated_at = ? "
                "WHERE approved IS NULL AND deleted = 0 AND superseded_by IS NULL",
                (_utcnow(),),
            )
            conn.commit()
            return cursor.rowcount

    # ── Portable export / import ──────────────────────────────

    def export_json(self, approved_only: bool = True) -> str:
        """Serialise facts to a portable JSON document (user-owned memory)."""
        facts = self.get_active(limit=100000, approved=1 if approved_only else None)
        payload = {
            "version": self.SCHEMA_VERSION,
            "exported_at": _utcnow(),
            "facts": [f.to_dict() for f in facts],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def export_markdown(self) -> str:
        """Human-readable 'what it knows about me' export (approved facts only)."""
        facts = self.get_active(limit=100000, approved=1)
        lines = ["# NAVIG — what I remember about you", ""]
        by_cat: dict[str, list[KeyFact]] = {}
        for f in facts:
            by_cat.setdefault(f.category, []).append(f)
        for cat in sorted(by_cat):
            lines.append(f"## {cat}")
            for f in by_cat[cat]:
                lines.append(f"- {f.content}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def import_json(self, text: str, default_approved: int | None = 1) -> tuple[int, int]:
        """Import facts from an ``export_json`` document.

        Returns ``(added, merged)``. Each fact is routed through ``upsert`` so
        content-dedup is handled. *default_approved* applies when an imported fact
        carries no explicit approval.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid memory import JSON: {exc}") from exc
        raw_facts = data.get("facts", data) if isinstance(data, dict) else data
        added = merged = 0
        for raw in raw_facts or []:
            try:
                content = (raw.get("content") or "").strip()
                if not content:
                    continue
                existed = self._find_duplicate(content) is not None
                tags = raw.get("tags")
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except json.JSONDecodeError:
                        tags = []
                fact = KeyFact(
                    content=content,
                    category=raw.get("category", "context"),
                    tags=tags or [],
                    confidence=float(raw.get("confidence", 0.8) or 0.8),
                    source_platform=raw.get("source_platform", "import"),
                    approved=raw.get("approved", default_approved),
                )
                self.upsert(fact)
                merged += int(existed)
                added += int(not existed)
            except Exception as exc:  # noqa: BLE001
                logger.debug("skipping malformed imported fact: %s", exc)
        return added, merged

    def search_keyword(self, query: str, limit: int = 20) -> list[tuple[KeyFact, float]]:
        """
        Keyword search via FTS5.  Returns (fact, rank) pairs.
        Falls back to LIKE if FTS5 is unavailable.
        """
        conn = self._conn()
        if self._fts_available:
            try:
                rows = conn.execute(
                    """
                    SELECT kf.*, fts.rank
                    FROM key_facts_fts fts
                    JOIN key_facts kf ON kf.id = fts.fact_id
                    WHERE key_facts_fts MATCH ?
                      AND kf.deleted = 0
                      AND kf.superseded_by IS NULL
                    ORDER BY fts.rank, kf.confidence DESC
                    LIMIT ?
                """,
                    (query, limit),
                ).fetchall()
                return [(KeyFact.from_row(r), abs(r["rank"])) for r in rows]
            except sqlite3.OperationalError:
                pass  # fall through to LIKE fallback
        # LIKE fallback
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT *, 1.0 as rank
            FROM key_facts
            WHERE deleted = 0
              AND superseded_by IS NULL
              AND (content LIKE ? OR tags LIKE ?)
            ORDER BY confidence DESC
            LIMIT ?
        """,
            (like, like, limit),
        ).fetchall()
        return [(KeyFact.from_row(r), 1.0) for r in rows]

    def search_vector(
        self,
        query_embedding: list[float],
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> list[tuple[KeyFact, float]]:
        """
        Vector similarity search using stored embeddings.
        Cosine similarity computed in Python (no sqlite-vec dependency needed for small stores).
        """
        # For key facts (typically <1000 entries), in-memory cosine is fast enough
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT * FROM key_facts
            WHERE deleted = 0
              AND superseded_by IS NULL
              AND embedding IS NOT NULL
        """
        ).fetchall()

        if not rows:
            return []

        results: list[tuple[KeyFact, float]] = []
        for row in rows:
            fact = KeyFact.from_row(row)
            if fact.embedding:
                sim = self._cosine_similarity(query_embedding, fact.embedding)
                if sim >= min_similarity:
                    results.append((fact, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def soft_delete(self, fact_id: str) -> bool:
        """Mark a fact as deleted.  Reversible."""
        with self._write_lock:
            conn = self._conn()
            cursor = conn.execute(
                "UPDATE key_facts SET deleted = 1, updated_at = ? WHERE id = ?",
                (_utcnow(), fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def restore(self, fact_id: str) -> bool:
        """Restore a soft-deleted fact."""
        with self._write_lock:
            conn = self._conn()
            cursor = conn.execute(
                "UPDATE key_facts SET deleted = 0, updated_at = ? WHERE id = ?",
                (_utcnow(), fact_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def supersede(self, old_id: str, new_fact: KeyFact) -> KeyFact:
        """Replace a fact with a new version.  Old fact is marked superseded."""
        with self._write_lock:
            self._conn().execute(
                "UPDATE key_facts SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_fact.id, _utcnow(), old_id),
            )
            self._conn().commit()
        return self.upsert(new_fact)

    def update_content(self, fact_id: str, new_content: str) -> KeyFact | None:
        """Update an existing fact's content in-place."""
        fact = self.get(fact_id)
        if not fact:
            return None
        fact.content = new_content
        fact.updated_at = _utcnow()
        if self.embedding_provider:
            try:
                fact.embedding = self.embedding_provider.embed_text(new_content)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        with self._write_lock:
            self._update_row(fact)
            self._update_fts(fact)
            self._conn().commit()
        return fact

    def record_access(self, fact_ids: list[str]) -> None:
        """Bump access_count and last_accessed for retrieved facts."""
        if not fact_ids:
            return
        now = _utcnow()
        with self._write_lock:
            conn = self._conn()
            conn.executemany(
                "UPDATE key_facts SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                [(now, fid) for fid in fact_ids],
            )
            conn.commit()

    def purge_deleted(self, older_than_days: int = 30) -> int:
        """Hard-delete facts that were soft-deleted more than N days ago."""
        # Approximation: check updated_at
        with self._write_lock:
            conn = self._conn()
            cursor = conn.execute(
                """DELETE FROM key_facts
                   WHERE deleted = 1
                     AND julianday('now') - julianday(updated_at) > ?""",
                (older_than_days,),
            )
            conn.commit()
            return cursor.rowcount

    def get_stats(self) -> dict[str, Any]:
        """Return store statistics."""
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) FROM key_facts").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM key_facts WHERE deleted = 0 AND superseded_by IS NULL"
        ).fetchone()[0]
        deleted = conn.execute("SELECT COUNT(*) FROM key_facts WHERE deleted = 1").fetchone()[0]
        superseded = conn.execute(
            "SELECT COUNT(*) FROM key_facts WHERE superseded_by IS NOT NULL"
        ).fetchone()[0]
        by_category = dict(
            conn.execute(
                "SELECT category, COUNT(*) FROM key_facts WHERE deleted = 0 AND superseded_by IS NULL GROUP BY category"
            ).fetchall()
        )
        return {
            "total": total,
            "active": active,
            "deleted": deleted,
            "superseded": superseded,
            "by_category": by_category,
            "db_path": str(self.db_path),
        }

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ── Private helpers ───────────────────────────────────────

    def _find_duplicate(self, content: str) -> KeyFact | None:
        """Find an active fact with very similar content (exact or near-match)."""
        # Exact match first
        row = (
            self._conn()
            .execute(
                "SELECT * FROM key_facts WHERE content = ? AND deleted = 0 AND superseded_by IS NULL",
                (content,),
            )
            .fetchone()
        )
        if row:
            return KeyFact.from_row(row)

        # Normalized match (strip whitespace + lowercase)
        normalized = " ".join(content.lower().split())
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM key_facts WHERE deleted = 0 AND superseded_by IS NULL LIMIT 500"
            )
            .fetchall()
        )
        for r in rows:
            existing_norm = " ".join(r["content"].lower().split())
            if existing_norm == normalized:
                return KeyFact.from_row(r)
            # High overlap check (Jaccard similarity > 0.85)
            if self._jaccard(normalized.split(), existing_norm.split()) > 0.85:
                return KeyFact.from_row(r)

        return None

    def _update_row(self, fact: KeyFact) -> None:
        d = fact.to_dict()
        self._conn().execute(
            """
            UPDATE key_facts SET
                content = :content,
                category = :category,
                tags = :tags,
                confidence = :confidence,
                updated_at = :updated_at,
                superseded_by = :superseded_by,
                deleted = :deleted,
                access_count = :access_count,
                last_accessed = :last_accessed,
                embedding = :embedding,
                metadata = :metadata,
                approved = :approved
            WHERE id = :id
        """,
            d,
        )

    def _update_fts(self, fact: KeyFact) -> None:
        """Update FTS index (uses current thread-local connection, no commit)."""
        self._update_fts_conn(self._conn(), fact)

    def _update_fts_conn(self, conn: sqlite3.Connection, fact: KeyFact) -> None:
        """Update FTS index on *conn* without committing."""
        if not self._fts_available:
            return
        try:
            conn.execute(
                "DELETE FROM key_facts_fts WHERE fact_id = ?",
                (fact.id,),
            )
            tags_str = " ".join(fact.tags) if fact.tags else ""
            conn.execute(
                "INSERT INTO key_facts_fts (fact_id, content, tags, category) VALUES (?, ?, ?, ?)",
                (fact.id, fact.content, tags_str, fact.category),
            )
        except sqlite3.OperationalError as exc:
            logger.debug("FTS update failed: %s", exc)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x * x for x in a) ** 0.5
        mag_b = sum(x * x for x in b) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _jaccard(a: list[str], b: list[str]) -> float:
        sa, sb = set(a), set(b)
        union = sa | sb
        if not union:
            return 0.0
        return len(sa & sb) / len(union)


# ── Module-level singleton ────────────────────────────────────

_store_instance: KeyFactStore | None = None
_store_lock = threading.Lock()


def get_key_fact_store(
    db_path: Path | None = None,
    embedding_provider: Any | None = None,
) -> KeyFactStore:
    """Get or create the singleton KeyFactStore."""
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            _store_instance = KeyFactStore(
                db_path=db_path,
                embedding_provider=embedding_provider,
            )
    return _store_instance


def reset_key_fact_store() -> None:
    """Reset singleton (for testing)."""
    global _store_instance
    with _store_lock:
        if _store_instance:
            _store_instance.close()
        _store_instance = None
