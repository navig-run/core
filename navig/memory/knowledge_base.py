"""
Persistent knowledge base with TTL support.

Stores structured knowledge entries with:
- Automatic expiration (TTL)
- Tag-based organization
- Vector embeddings for semantic search
- Source tracking
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from navig.memory.embeddings import EmbeddingProvider


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger

        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception as _e:  # noqa: BLE001
        import sys

        print(
            f"[navig/memory/knowledge_base] logger init failed ({type(_e).__name__}): {_e}",
            file=sys.stderr,
        )


@dataclass
class KnowledgeEntry:
    """A knowledge base entry."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    key: str = ""  # Unique key for deduplication
    content: str = ""
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = ""  # Where this knowledge came from
    created_at: datetime = field(default_factory=datetime.now)  # utcnow deprecated in Py3.12+
    expires_at: datetime | None = None
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "key": self.key,
            "content": self.content,
            "summary": self.summary,
            "tags": json.dumps(self.tags),
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": json.dumps(self.metadata),
            "embedding": json.dumps(self.embedding) if self.embedding else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeEntry:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            key=data["key"],
            content=data["content"],
            summary=data.get("summary"),
            tags=(json.loads(data["tags"]) if isinstance(data["tags"], str) else data["tags"]),
            source=data.get("source", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=(
                datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
            ),
            metadata=(
                json.loads(data["metadata"])
                if isinstance(data["metadata"], str)
                else data.get("metadata", {})
            ),
            embedding=json.loads(data["embedding"]) if data.get("embedding") else None,
        )

    @property
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


class KnowledgeBase:
    """
    Persistent knowledge store with vector search.

    Stores structured knowledge with:
    - Unique keys for upsert operations
    - TTL-based expiration
    - Tag-based filtering
    - Optional vector embeddings for semantic search

    Usage:
        from navig.platform import paths
        kb = KnowledgeBase(
            db_path=paths.data_dir() / 'knowledge.db',
            embedding_provider=LocalEmbeddingProvider(),
        )

        # Add knowledge
        entry = KnowledgeEntry(
            key='project-info',
            content='This project uses Python 3.10+',
            tags=['project', 'python'],
            source='user',
        )
        kb.upsert(entry)

        # Search
        results = kb.search('python version', limit=5)
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        db_path: Path,
        embedding_provider: EmbeddingProvider | None = None,
        auto_expire: bool = True,
    ):
        self.db_path = db_path
        self.embedding_provider = embedding_provider
        self.auto_expire = auto_expire
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._init_schema()

        # Clean expired entries on startup
        if auto_expire:
            self._clean_expired()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                metadata TEXT DEFAULT '{}',
                embedding TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_key
                ON knowledge(key);
            CREATE INDEX IF NOT EXISTS idx_knowledge_source
                ON knowledge(source);
            CREATE INDEX IF NOT EXISTS idx_knowledge_expires
                ON knowledge(expires_at);
        """
        )

        conn.commit()
        _debug_log(f"KnowledgeBase initialized at {self.db_path}")

    def _clean_expired(self) -> int:
        """Remove expired entries."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        with self._lock:
            cursor = conn.execute(
                "DELETE FROM knowledge WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()

        deleted = cursor.rowcount
        if deleted > 0:
            _debug_log(f"Cleaned {deleted} expired knowledge entries")
        return deleted

    def upsert(
        self,
        entry: KnowledgeEntry,
        compute_embedding: bool = True,
    ) -> KnowledgeEntry:
        """
        Insert or update a knowledge entry.

        If an entry with the same key exists, it will be updated.

        Args:
            entry: The knowledge entry
            compute_embedding: Whether to compute embedding vector

        Returns:
            The stored entry
        """
        # Compute embedding if provider available
        if compute_embedding and self.embedding_provider and not entry.embedding:
            text = entry.summary or entry.content
            entry.embedding = self.embedding_provider.embed_text(text)

        conn = self._get_conn()
        data = entry.to_dict()

        with self._lock:
            conn.execute(
                """
                INSERT INTO knowledge (
                    id, key, content, summary, tags, source,
                    created_at, expires_at, metadata, embedding
                ) VALUES (
                    :id, :key, :content, :summary, :tags, :source,
                    :created_at, :expires_at, :metadata, :embedding
                )
                ON CONFLICT(key) DO UPDATE SET
                    content = excluded.content,
                    summary = excluded.summary,
                    tags = excluded.tags,
                    source = excluded.source,
                    expires_at = excluded.expires_at,
                    metadata = excluded.metadata,
                    embedding = excluded.embedding
            """,
                data,
            )
            conn.commit()

        _debug_log(f"Upserted knowledge entry: {entry.key}")
        return entry

    def get(self, key: str) -> KnowledgeEntry | None:
        """Get entry by key."""
        conn = self._get_conn()

        cursor = conn.execute("SELECT * FROM knowledge WHERE key = ?", (key,))
        row = cursor.fetchone()

        if not row:
            return None

        entry = KnowledgeEntry.from_dict(dict(row))

        # Check expiration
        if entry.is_expired:
            self.delete(key)
            return None

        return entry

    def get_by_id(self, id: str) -> KnowledgeEntry | None:
        """Get entry by ID."""
        conn = self._get_conn()

        cursor = conn.execute("SELECT * FROM knowledge WHERE id = ?", (id,))
        row = cursor.fetchone()

        if not row:
            return None

        entry = KnowledgeEntry.from_dict(dict(row))

        if entry.is_expired:
            return None

        return entry

    def delete(self, key: str) -> bool:
        """Delete entry by key."""
        conn = self._get_conn()

        with self._lock:
            cursor = conn.execute("DELETE FROM knowledge WHERE key = ?", (key,))
            conn.commit()

        return cursor.rowcount > 0

    def list_by_tag(
        self,
        tag: str,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        """Get all entries with a specific tag."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        # JSON contains check
        cursor = conn.execute(
            """
            SELECT * FROM knowledge
            WHERE tags LIKE ?
            AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (f'%"{tag}"%', now, limit),
        )

        return [KnowledgeEntry.from_dict(dict(row)) for row in cursor.fetchall()]

    def list_by_source(
        self,
        source: str,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        """Get all entries from a specific source."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        cursor = conn.execute(
            """
            SELECT * FROM knowledge
            WHERE source = ?
            AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (source, now, limit),
        )

        return [KnowledgeEntry.from_dict(dict(row)) for row in cursor.fetchall()]

    def search(
        self,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
        tags: list[str] | None = None,
    ) -> list[tuple[KnowledgeEntry, float]]:
        """
        Semantic search across knowledge base.

        Args:
            query: Search query
            limit: Maximum results
            min_similarity: Minimum cosine similarity threshold
            tags: Optional tag filter

        Returns:
            List of (entry, similarity) tuples sorted by relevance
        """
        if not self.embedding_provider:
            # Fall back to text search
            return [(entry, 1.0) for entry in self.text_search(query, limit, tags)]

        # Get query embedding
        query_embedding = self.embedding_provider.embed_text(query)

        # Get all entries (with optional tag filter)
        conn = self._get_conn()
        now = datetime.now().isoformat()

        if tags:
            tag_conditions = " AND ".join(f"tags LIKE '%\"{tag}\"%'" for tag in tags)
            cursor = conn.execute(
                f"""
                SELECT * FROM knowledge
                WHERE embedding IS NOT NULL
                AND (expires_at IS NULL OR expires_at > ?)
                AND {tag_conditions}
            """,
                (now,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM knowledge
                WHERE embedding IS NOT NULL
                AND (expires_at IS NULL OR expires_at > ?)
            """,
                (now,),
            )

        # Compute similarities
        results = []
        for row in cursor.fetchall():
            entry = KnowledgeEntry.from_dict(dict(row))
            if entry.embedding:
                similarity = self.embedding_provider.similarity(
                    query_embedding,
                    entry.embedding,
                )
                if similarity >= min_similarity:
                    results.append((entry, similarity))

        # Sort by similarity
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    def text_search(
        self,
        query: str,
        limit: int = 10,
        tags: list[str] | None = None,
    ) -> list[KnowledgeEntry]:
        """
        Full-text search (fallback when no embeddings).

        Args:
            query: Search query
            limit: Maximum results
            tags: Optional tag filter

        Returns:
            Matching entries
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        pattern = f"%{query}%"

        sql = """
            SELECT * FROM knowledge
            WHERE (content LIKE ? OR summary LIKE ?)
            AND (expires_at IS NULL OR expires_at > ?)
        """
        params = [pattern, pattern, now]

        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(sql, params)

        return [KnowledgeEntry.from_dict(dict(row)) for row in cursor.fetchall()]

    def add_with_ttl(
        self,
        key: str,
        content: str,
        ttl_hours: int = 24,
        **kwargs,
    ) -> KnowledgeEntry:
        """
        Add knowledge with automatic expiration.

        Args:
            key: Unique key
            content: Knowledge content
            ttl_hours: Hours until expiration
            **kwargs: Additional KnowledgeEntry fields

        Returns:
            Stored entry
        """
        expires_at = datetime.now() + timedelta(hours=ttl_hours)

        entry = KnowledgeEntry(
            key=key,
            content=content,
            expires_at=expires_at,
            **kwargs,
        )

        return self.upsert(entry)

    def count(self) -> int:
        """Get total number of non-expired entries."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        cursor = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE expires_at IS NULL OR expires_at > ?",
            (now,),
        )
        return cursor.fetchone()[0]

    def clear(self) -> int:
        """Delete all entries."""
        conn = self._get_conn()

        with self._lock:
            cursor = conn.execute("DELETE FROM knowledge")
            conn.commit()

        deleted = cursor.rowcount
        _debug_log(f"Cleared knowledge base ({deleted} entries)")
        return deleted

    def export_entries(self) -> list[dict]:
        """Export all entries as dictionaries."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        cursor = conn.execute(
            "SELECT * FROM knowledge WHERE expires_at IS NULL OR expires_at > ?", (now,)
        )

        return [dict(row) for row in cursor.fetchall()]

    def import_entries(
        self,
        entries: list[dict],
        overwrite: bool = False,
    ) -> int:
        """
        Import entries from dictionaries.

        Args:
            entries: List of entry dictionaries
            overwrite: Whether to overwrite existing keys

        Returns:
            Number of entries imported
        """
        imported = 0

        for data in entries:
            try:
                entry = KnowledgeEntry.from_dict(data)

                if not overwrite and self.get(entry.key):
                    continue

                self.upsert(entry, compute_embedding=False)
                imported += 1

            except Exception as e:
                _debug_log(f"Failed to import entry: {e}")

        return imported

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
