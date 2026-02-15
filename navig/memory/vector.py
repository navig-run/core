"""
Vector search support via sqlite-vec.

Provides optional ANN (Approximate Nearest Neighbor) search using the
sqlite-vec extension.  Falls back gracefully when the extension is
unavailable — callers should check ``VectorIndex.available`` before
using vector methods.

Lifecycle: created by ``MemoryStorage`` and shares its SQLite connection.
"""

from __future__ import annotations

import json
import logging
import struct
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Module-level availability flag ────────────────────────────

_VEC_AVAILABLE: Optional[bool] = None


def _check_vec() -> bool:
    """Return True if sqlite-vec can be loaded."""
    global _VEC_AVAILABLE
    if _VEC_AVAILABLE is not None:
        return _VEC_AVAILABLE
    try:
        import sqlite_vec  # noqa: F401

        _VEC_AVAILABLE = True
    except ImportError:
        _VEC_AVAILABLE = False
    return _VEC_AVAILABLE


# ── Helpers ───────────────────────────────────────────────────


def floats_to_blob(floats: List[float]) -> bytes:
    """Pack a list of floats into a little-endian float32 BLOB."""
    return struct.pack(f"<{len(floats)}f", *floats)


def blob_to_floats(blob: bytes) -> List[float]:
    """Unpack a little-endian float32 BLOB into a list of floats."""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


# ── VectorIndex ───────────────────────────────────────────────


class VectorIndex:
    """
    Wraps a ``chunks_vec`` vec0 virtual table living inside an existing
    SQLite database (typically ``memory/index.db``).

    Parameters
    ----------
    conn : sqlite3.Connection
        An *already-opened* connection. The caller owns the connection
        lifetime — ``VectorIndex`` never closes it.
    dimensions : int
        Embedding dimensionality (default 1536 for OpenAI text-embedding-3-small).
    """

    def __init__(self, conn, *, dimensions: int = 1536):
        self._conn = conn
        self.dimensions = dimensions
        self.available = False

        if not _check_vec():
            logger.debug("sqlite-vec not installed — vector search disabled")
            return

        try:
            import sqlite_vec

            sqlite_vec.load(self._conn)
            self.available = True
            self._ensure_table()
            logger.debug("sqlite-vec loaded — vector search enabled (dim=%d)", dimensions)
        except Exception as exc:
            logger.warning("Failed to load sqlite-vec: %s", exc)

    # ── Schema ────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                chunk_id TEXT PRIMARY KEY,
                embedding float[{self.dimensions}]
            )
            """
        )
        self._conn.commit()

    # ── Write ─────────────────────────────────────────────────

    def upsert(self, chunk_id: str, embedding: List[float]) -> None:
        """Insert or replace a single embedding."""
        if not self.available:
            return
        blob = floats_to_blob(embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, blob),
        )
        self._conn.commit()

    def upsert_batch(self, items: List[Tuple[str, List[float]]]) -> int:
        """Insert/replace many embeddings in one transaction."""
        if not self.available or not items:
            return 0
        data = [(cid, floats_to_blob(emb)) for cid, emb in items]
        self._conn.executemany(
            "INSERT OR REPLACE INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
            data,
        )
        self._conn.commit()
        return len(data)

    def delete(self, chunk_id: str) -> None:
        if not self.available:
            return
        self._conn.execute(
            "DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk_id,)
        )
        self._conn.commit()

    # ── Search ────────────────────────────────────────────────

    def search(
        self,
        query_embedding: List[float],
        *,
        limit: int = 20,
    ) -> List[Tuple[str, float]]:
        """
        Top-K approximate nearest-neighbour search.

        Returns list of ``(chunk_id, distance)`` tuples sorted by ascending
        distance (lower = more similar for cosine).
        """
        if not self.available:
            return []
        blob = floats_to_blob(query_embedding)
        cursor = self._conn.execute(
            """
            SELECT chunk_id, distance
            FROM chunks_vec
            WHERE embedding MATCH ?
            AND k = ?
            ORDER BY distance
            """,
            (blob, limit),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    # ── Migration ─────────────────────────────────────────────

    def migrate_text_embeddings(self, *, batch_size: int = 500) -> int:
        """
        One-time migration: read TEXT JSON embeddings from the ``chunks``
        table and populate the ``chunks_vec`` virtual table.

        Returns the number of embeddings migrated.
        """
        if not self.available:
            return 0

        rows = self._conn.execute(
            "SELECT id, embedding FROM chunks "
            "WHERE embedding IS NOT NULL AND typeof(embedding) = 'text'"
        ).fetchall()

        if not rows:
            return 0

        migrated = 0
        batch: List[Tuple[str, bytes]] = []

        for row in rows:
            try:
                floats = json.loads(row[1] if isinstance(row, tuple) else row["embedding"])
                chunk_id = row[0] if isinstance(row, tuple) else row["id"]
                blob = floats_to_blob(floats)
                batch.append((chunk_id, blob))

                if len(batch) >= batch_size:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                        batch,
                    )
                    migrated += len(batch)
                    batch.clear()
            except (json.JSONDecodeError, struct.error) as exc:
                logger.warning("Skip embedding migration for chunk %s: %s", row[0], exc)

        if batch:
            self._conn.executemany(
                "INSERT OR REPLACE INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                batch,
            )
            migrated += len(batch)

        self._conn.commit()
        logger.info("Migrated %d TEXT embeddings → vec0", migrated)
        return migrated

    # ── Stats ─────────────────────────────────────────────────

    def count(self) -> int:
        if not self.available:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()
        return row[0] if row else 0

    def __repr__(self) -> str:
        status = "active" if self.available else "unavailable"
        return f"<VectorIndex dim={self.dimensions} status={status}>"
