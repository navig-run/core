"""
Memory Storage - SQLite-backed chunk and file metadata storage.

Implements advanced memory persistence patterns:
- SQLite with WAL mode for concurrent access
- FTS5 virtual table for BM25 keyword search
- Embedding cache to avoid re-embedding unchanged content
- File metadata tracking with modification timestamps
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger
        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


@dataclass
class MemoryChunk:
    """A chunk of indexed content from a file."""

    id: str  # SHA256 hash of content
    file_path: str  # Relative path from memory directory
    content: str  # The text content
    line_start: int  # Starting line number (1-based)
    line_end: int  # Ending line number (1-based)
    token_count: int  # Estimated tokens
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'file_path': self.file_path,
            'content': self.content,
            'line_start': self.line_start,
            'line_end': self.line_end,
            'token_count': self.token_count,
            'embedding': json.dumps(self.embedding) if self.embedding else None,
            'metadata': json.dumps(self.metadata),
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MemoryChunk':
        return cls(
            id=data['id'],
            file_path=data['file_path'],
            content=data['content'],
            line_start=data['line_start'],
            line_end=data['line_end'],
            token_count=data.get('token_count', 0),
            embedding=json.loads(data['embedding']) if data.get('embedding') else None,
            metadata=json.loads(data['metadata']) if isinstance(data['metadata'], str) else data.get('metadata', {}),
            created_at=data.get('created_at', datetime.utcnow().isoformat()),
        )


@dataclass
class FileMetadata:
    """Metadata about an indexed file."""

    file_path: str  # Relative path from memory directory
    file_hash: str  # SHA256 hash of file content
    last_modified: str  # File modification timestamp
    chunk_count: int  # Number of chunks from this file
    total_tokens: int  # Total tokens in all chunks
    indexed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            'file_path': self.file_path,
            'file_hash': self.file_hash,
            'last_modified': self.last_modified,
            'chunk_count': self.chunk_count,
            'total_tokens': self.total_tokens,
            'indexed_at': self.indexed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FileMetadata':
        return cls(
            file_path=data['file_path'],
            file_hash=data['file_hash'],
            last_modified=data['last_modified'],
            chunk_count=data.get('chunk_count', 0),
            total_tokens=data.get('total_tokens', 0),
            indexed_at=data.get('indexed_at', datetime.utcnow().isoformat()),
        )


class MemoryStorage:
    """
    SQLite-backed storage for memory chunks and file metadata.
    
    Features:
    - WAL mode for concurrent read/write
    - FTS5 full-text search for BM25 ranking
    - Embedding cache to avoid re-embedding unchanged content
    - Thread-safe with connection pooling
    
    Usage:
        storage = MemoryStorage(Path.home() / '.navig' / 'memory' / 'index.db')
        
        # Store chunks
        storage.upsert_chunks(chunks)
        
        # Search by text (BM25)
        results = storage.search_fts("docker compose", limit=10)
        
        # Get all chunks with embeddings
        chunks = storage.get_all_chunks_with_embeddings()
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path, *, embedding_dimensions: int = 1536):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._embedding_dim = embedding_dimensions
        self._vec: Optional['VectorIndex'] = None  # lazy init per connection

        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute('PRAGMA journal_mode=WAL')
            self._local.conn.execute('PRAGMA synchronous=NORMAL')
            self._local.conn.execute('PRAGMA cache_size=-64000')  # 64MB cache
        return self._local.conn

    def _init_schema(self) -> None:
        """Initialize database schema with FTS5."""
        conn = self._get_conn()

        conn.executescript('''
            -- Schema version tracking
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            
            -- File metadata table
            CREATE TABLE IF NOT EXISTS files (
                file_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                last_modified TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                indexed_at TEXT NOT NULL
            );
            
            -- Chunks table
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                content TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                token_count INTEGER DEFAULT 0,
                embedding TEXT,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (file_path) REFERENCES files(file_path) ON DELETE CASCADE
            );
            
            -- Indexes for efficient queries
            CREATE INDEX IF NOT EXISTS idx_chunks_file 
                ON chunks(file_path);
            CREATE INDEX IF NOT EXISTS idx_chunks_tokens 
                ON chunks(token_count);
            CREATE INDEX IF NOT EXISTS idx_files_hash 
                ON files(file_hash);
            CREATE INDEX IF NOT EXISTS idx_files_modified 
                ON files(last_modified);
            
            -- FTS5 virtual table for full-text search (BM25)
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                file_path,
                content=chunks,
                content_rowid=rowid
            );
            
            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, content, file_path) 
                VALUES (NEW.rowid, NEW.content, NEW.file_path);
            END;
            
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content, file_path) 
                VALUES('delete', OLD.rowid, OLD.content, OLD.file_path);
            END;
            
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content, file_path) 
                VALUES('delete', OLD.rowid, OLD.content, OLD.file_path);
                INSERT INTO chunks_fts(rowid, content, file_path) 
                VALUES (NEW.rowid, NEW.content, NEW.file_path);
            END;
            
            -- Embedding cache table
            CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                model_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            
            -- Global metadata/stats
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        ''')

        # Set schema version if not exists
        cursor = conn.execute('SELECT version FROM schema_version LIMIT 1')
        if cursor.fetchone() is None:
            conn.execute(
                'INSERT INTO schema_version (version) VALUES (?)',
                (self.SCHEMA_VERSION,)
            )

        conn.commit()
        _debug_log(f"MemoryStorage initialized at {self.db_path}")

    # ---------- File Operations ----------

    def upsert_file_metadata(self, metadata: FileMetadata) -> None:
        """Insert or update file metadata."""
        conn = self._get_conn()
        data = metadata.to_dict()

        with self._lock:
            conn.execute('''
                INSERT INTO files (file_path, file_hash, last_modified, chunk_count, total_tokens, indexed_at)
                VALUES (:file_path, :file_hash, :last_modified, :chunk_count, :total_tokens, :indexed_at)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    last_modified = excluded.last_modified,
                    chunk_count = excluded.chunk_count,
                    total_tokens = excluded.total_tokens,
                    indexed_at = excluded.indexed_at
            ''', data)
            conn.commit()

    def get_file_metadata(self, file_path: str) -> Optional[FileMetadata]:
        """Get file metadata by path."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT * FROM files WHERE file_path = ?',
            (file_path,)
        )
        row = cursor.fetchone()
        return FileMetadata.from_dict(dict(row)) if row else None

    def get_all_files(self) -> List[FileMetadata]:
        """Get all indexed files."""
        conn = self._get_conn()
        cursor = conn.execute('SELECT * FROM files ORDER BY indexed_at DESC')
        return [FileMetadata.from_dict(dict(row)) for row in cursor.fetchall()]

    def delete_file(self, file_path: str) -> int:
        """Delete a file and all its chunks."""
        conn = self._get_conn()

        with self._lock:
            # Delete chunks first (foreign key)
            cursor = conn.execute(
                'DELETE FROM chunks WHERE file_path = ?',
                (file_path,)
            )
            deleted_chunks = cursor.rowcount

            conn.execute(
                'DELETE FROM files WHERE file_path = ?',
                (file_path,)
            )
            conn.commit()

        return deleted_chunks

    def file_needs_reindex(self, file_path: str, current_hash: str) -> bool:
        """Check if file needs reindexing based on hash."""
        metadata = self.get_file_metadata(file_path)
        return metadata is None or metadata.file_hash != current_hash

    # ---------- Chunk Operations ----------

    def upsert_chunks(self, chunks: List[MemoryChunk]) -> int:
        """Insert or update multiple chunks."""
        if not chunks:
            return 0

        conn = self._get_conn()

        with self._lock:
            for chunk in chunks:
                data = chunk.to_dict()
                conn.execute('''
                    INSERT INTO chunks (id, file_path, content, line_start, line_end, 
                                       token_count, embedding, metadata, created_at)
                    VALUES (:id, :file_path, :content, :line_start, :line_end,
                            :token_count, :embedding, :metadata, :created_at)
                    ON CONFLICT(id) DO UPDATE SET
                        content = excluded.content,
                        line_start = excluded.line_start,
                        line_end = excluded.line_end,
                        token_count = excluded.token_count,
                        embedding = excluded.embedding,
                        metadata = excluded.metadata
                ''', data)
            conn.commit()

        return len(chunks)

    def get_chunk(self, chunk_id: str) -> Optional[MemoryChunk]:
        """Get a chunk by ID."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT * FROM chunks WHERE id = ?',
            (chunk_id,)
        )
        row = cursor.fetchone()
        return MemoryChunk.from_dict(dict(row)) if row else None

    def get_chunks_for_file(self, file_path: str) -> List[MemoryChunk]:
        """Get all chunks for a file."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT * FROM chunks WHERE file_path = ? ORDER BY line_start',
            (file_path,)
        )
        return [MemoryChunk.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_all_chunks(self) -> Iterator[MemoryChunk]:
        """Iterate over all chunks."""
        conn = self._get_conn()
        cursor = conn.execute('SELECT * FROM chunks')
        for row in cursor:
            yield MemoryChunk.from_dict(dict(row))

    def get_all_chunks_with_embeddings(self) -> List[MemoryChunk]:
        """Get all chunks that have embeddings."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT * FROM chunks WHERE embedding IS NOT NULL'
        )
        return [MemoryChunk.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_chunks_without_embeddings(self) -> List[MemoryChunk]:
        """Get chunks that need embedding generation."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT * FROM chunks WHERE embedding IS NULL'
        )
        return [MemoryChunk.from_dict(dict(row)) for row in cursor.fetchall()]

    def update_chunk_embedding(self, chunk_id: str, embedding: List[float]) -> None:
        """Update the embedding for a specific chunk."""
        conn = self._get_conn()

        with self._lock:
            conn.execute(
                'UPDATE chunks SET embedding = ? WHERE id = ?',
                (json.dumps(embedding), chunk_id)
            )
            conn.commit()

        # Sync to vec0 table (best-effort)
        try:
            vec = self._get_vec()
            if vec.available:
                vec.upsert(chunk_id, embedding)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def delete_chunks_for_file(self, file_path: str) -> int:
        """Delete all chunks for a file."""
        conn = self._get_conn()

        with self._lock:
            cursor = conn.execute(
                'DELETE FROM chunks WHERE file_path = ?',
                (file_path,)
            )
            conn.commit()

        return cursor.rowcount

    # ---------- Full-Text Search (BM25) ----------

    def search_fts(
        self,
        query: str,
        limit: int = 10,
        file_filter: Optional[str] = None,
    ) -> List[tuple[MemoryChunk, float]]:
        """
        Search using FTS5 with BM25 ranking.
        
        Args:
            query: Search query (supports FTS5 syntax)
            limit: Maximum results
            file_filter: Optional file path pattern filter
            
        Returns:
            List of (chunk, bm25_score) tuples, higher scores = more relevant
        """
        conn = self._get_conn()

        # Escape special FTS characters and build query
        # FTS5 uses negative BM25 (more negative = more relevant)
        if file_filter:
            cursor = conn.execute('''
                SELECT c.*, -bm25(chunks_fts) as score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                AND c.file_path LIKE ?
                ORDER BY score DESC
                LIMIT ?
            ''', (query, file_filter, limit))
        else:
            cursor = conn.execute('''
                SELECT c.*, -bm25(chunks_fts) as score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY score DESC
                LIMIT ?
            ''', (query, limit))

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            score = row_dict.pop('score', 0.0)
            chunk = MemoryChunk.from_dict(row_dict)
            results.append((chunk, score))

        return results

    def search_fts_simple(
        self,
        query: str,
        limit: int = 10,
    ) -> List[tuple[MemoryChunk, float]]:
        """
        Simple keyword search - splits query into words and matches any.
        More forgiving than strict FTS5 syntax.
        """
        # Build OR query from words
        words = query.split()
        if not words:
            return []

        # Escape quotes and build FTS query
        escaped_words = [w.replace('"', '""') for w in words]
        fts_query = ' OR '.join(f'"{w}"' for w in escaped_words)

        try:
            return self.search_fts(fts_query, limit)
        except sqlite3.OperationalError:
            # If query syntax fails, fall back to LIKE search
            return self._search_like(query, limit)

    def _search_like(
        self,
        query: str,
        limit: int = 10,
    ) -> List[tuple[MemoryChunk, float]]:
        """Fallback LIKE-based search."""
        conn = self._get_conn()

        pattern = f'%{query}%'
        cursor = conn.execute('''
            SELECT * FROM chunks 
            WHERE content LIKE ?
            ORDER BY token_count DESC
            LIMIT ?
        ''', (pattern, limit))

        return [
            (MemoryChunk.from_dict(dict(row)), 1.0)
            for row in cursor.fetchall()
        ]

    # ---------- Embedding Cache ----------

    def get_cached_embedding(
        self,
        content_hash: str,
        model_name: str,
    ) -> Optional[List[float]]:
        """Get cached embedding for content hash."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT embedding FROM embedding_cache WHERE content_hash = ? AND model_name = ?',
            (content_hash, model_name)
        )
        row = cursor.fetchone()
        return json.loads(row['embedding']) if row else None

    def cache_embedding(
        self,
        content_hash: str,
        embedding: List[float],
        model_name: str,
    ) -> None:
        """Cache an embedding for future use."""
        self.upsert_embedding_cache([(content_hash, embedding, model_name)])

    def upsert_embedding_cache(
        self,
        entries: List[Tuple[str, List[float], str]],
    ) -> int:
        """
        Batch update embedding cache.
        
        Args:
            entries: List of (content_hash, embedding, model_name) tuples
        """
        if not entries:
            return 0

        conn = self._get_conn()
        created_at = datetime.utcnow().isoformat()

        # Prepare data
        data = [
            (h, json.dumps(e), m, created_at)
            for h, e, m in entries
        ]

        with self._lock:
            conn.executemany('''
                INSERT INTO embedding_cache (content_hash, embedding, model_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    embedding = excluded.embedding,
                    model_name = excluded.model_name,
                    created_at = excluded.created_at
            ''', data)
            conn.commit()

        return len(entries)

    def update_chunk_embeddings(
        self,
        updates: List[Tuple[str, List[float]]],
    ) -> int:
        """
        Batch update chunk embeddings.
        
        Args:
            updates: List of (chunk_id, embedding) tuples
        """
        if not updates:
            return 0

        conn = self._get_conn()

        # Prepare data
        data = [
            (json.dumps(e), cid)
            for cid, e in updates
        ]

        with self._lock:
            conn.executemany('''
                UPDATE chunks SET embedding = ? WHERE id = ?
            ''', data)
            conn.commit()

        # Sync to vec0 table (best-effort)
        try:
            vec = self._get_vec()
            if vec.available:
                vec.upsert_batch(updates)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return len(updates)

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]

    # ---------- Metadata/Stats ----------

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata key-value pair."""
        conn = self._get_conn()

        with self._lock:
            conn.execute('''
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            ''', (key, json.dumps(value), datetime.utcnow().isoformat()))
            conn.commit()

    def get_metadata(self, key: str) -> Optional[Any]:
        """Get a metadata value."""
        conn = self._get_conn()
        cursor = conn.execute(
            'SELECT value FROM metadata WHERE key = ?',
            (key,)
        )
        row = cursor.fetchone()
        return json.loads(row['value']) if row else None

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        conn = self._get_conn()

        file_count = conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        chunk_count = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        total_tokens = conn.execute('SELECT SUM(token_count) FROM chunks').fetchone()[0] or 0
        embedded_count = conn.execute(
            'SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL'
        ).fetchone()[0]
        cache_count = conn.execute('SELECT COUNT(*) FROM embedding_cache').fetchone()[0]

        # Get database size
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            'file_count': file_count,
            'chunk_count': chunk_count,
            'total_tokens': total_tokens,
            'embedded_chunks': embedded_count,
            'embedding_cache_size': cache_count,
            'database_size_bytes': db_size,
            'database_size_mb': round(db_size / 1024 / 1024, 2),
        }

    # ---------- Maintenance ----------

    def vacuum(self) -> None:
        """Compact the database."""
        conn = self._get_conn()
        conn.execute('VACUUM')

    def clear_all(self) -> Dict[str, int]:
        """Clear all data and return counts."""
        conn = self._get_conn()

        stats = self.get_stats()

        with self._lock:
            conn.executescript('''
                DELETE FROM chunks;
                DELETE FROM files;
                DELETE FROM embedding_cache;
                DELETE FROM metadata;
            ''')
            conn.commit()

        self.vacuum()

        return {
            'files_deleted': stats['file_count'],
            'chunks_deleted': stats['chunk_count'],
            'cache_cleared': stats['embedding_cache_size'],
        }

    def clear_embedding_cache(self) -> int:
        """Clear only the embedding cache."""
        conn = self._get_conn()

        with self._lock:
            cursor = conn.execute('DELETE FROM embedding_cache')
            conn.commit()

        return cursor.rowcount

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
        self._vec = None

    # ---------- Vector Search (sqlite-vec) ----------

    def _get_vec(self):
        """Lazily init VectorIndex on first call (per main-thread conn)."""
        if self._vec is None:
            from navig.memory.vector import VectorIndex

            self._vec = VectorIndex(
                self._get_conn(), dimensions=self._embedding_dim
            )
        return self._vec

    @property
    def vec_available(self) -> bool:
        """True if sqlite-vec is loaded and vector search is possible."""
        return self._get_vec().available

    def vector_search(
        self,
        query_embedding: List[float],
        limit: int = 10,
    ) -> List[Tuple['MemoryChunk', float]]:
        """
        ANN vector search using sqlite-vec.

        Returns list of (chunk, distance) tuples sorted by ascending
        distance.  Raises RuntimeError if sqlite-vec is unavailable.
        """
        vec = self._get_vec()
        if not vec.available:
            raise RuntimeError(
                "sqlite-vec not available — install with: pip install sqlite-vec"
            )

        hits = vec.search(query_embedding, limit=limit)
        if not hits:
            return []

        conn = self._get_conn()
        results: List[Tuple[MemoryChunk, float]] = []
        for chunk_id, distance in hits:
            row = conn.execute(
                "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            if row:
                results.append((MemoryChunk.from_dict(dict(row)), distance))
        return results

    def hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        *,
        limit: int = 10,
        alpha: float = 0.3,
    ) -> List[Tuple['MemoryChunk', float]]:
        """
        Combined FTS5 BM25 + vector reranking.

        Score = α · BM25_norm + (1-α) · (1 - cosine_dist)

        Falls back to FTS-only if sqlite-vec is unavailable.
        """
        # FTS5 candidates
        fts_results = self.search_fts(query, limit=limit * 3)

        vec = self._get_vec()
        if not vec.available or not query_embedding:
            # Degrade gracefully to FTS-only
            return fts_results[:limit]

        # Vector candidates
        vec_hits = vec.search(query_embedding, limit=limit * 3)
        vec_map: Dict[str, float] = {cid: dist for cid, dist in vec_hits}

        # Normalise BM25 scores (0..1)
        max_bm25 = max((s for _, s in fts_results), default=1.0) or 1.0
        scored: Dict[str, Tuple[MemoryChunk, float]] = {}

        for chunk, bm25 in fts_results:
            norm_bm25 = bm25 / max_bm25
            vec_dist = vec_map.get(chunk.id, 1.0)  # default to max distance
            combined = alpha * norm_bm25 + (1 - alpha) * (1 - vec_dist)
            scored[chunk.id] = (chunk, combined)

        # Add vector-only hits not in FTS results
        conn = self._get_conn()
        for chunk_id, distance in vec_hits:
            if chunk_id not in scored:
                row = conn.execute(
                    "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
                ).fetchone()
                if row:
                    combined = (1 - alpha) * (1 - distance)
                    scored[chunk_id] = (MemoryChunk.from_dict(dict(row)), combined)

        # Sort by combined score descending (higher = better)
        ranked = sorted(scored.values(), key=lambda x: x[1], reverse=True)
        return ranked[:limit]

    def migrate_embeddings_to_vec(self) -> int:
        """
        One-time migration: convert TEXT JSON embeddings stored in the
        ``chunks`` table into the ``chunks_vec`` vec0 virtual table.

        Safe to call multiple times — only processes TEXT-type embeddings.
        Returns the count of embeddings migrated.
        """
        vec = self._get_vec()
        with self._lock:
            return vec.migrate_text_embeddings()
