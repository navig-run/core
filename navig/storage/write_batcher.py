"""
WriteBatcher — Time-and-count-triggered batch commit queue.

Groups INSERT/UPDATE operations into batches committed in a single
transaction.  Flushes when either the count threshold OR the time
window is reached (whichever comes first).

One batcher per database file.  Thread-safe.

Usage::

    batcher = WriteBatcher(conn, lock, batch_size=50, flush_interval_ms=100)
    batcher.enqueue("INSERT INTO t VALUES (?, ?)", (1, "a"))
    batcher.enqueue("INSERT INTO t VALUES (?, ?)", (2, "b"))
    # ... auto-flushed after 50 ops or 100ms

    # Manual flush for shutdown
    batcher.flush()
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _PendingWrite:
    """A single queued write operation."""

    sql: str
    params: tuple
    is_many: bool = False
    seq_params: list[tuple] | None = None


class WriteBatcher:
    """
    Batched, time-and-count-triggered commit queue for a single database.

    Parameters
    ----------
    get_conn : callable
        Returns the sqlite3.Connection to use for writes.
    lock : threading.Lock
        The write lock for this database (shared with the store).
    batch_size : int
        Maximum number of operations before forced flush.
    flush_interval_ms : float
        Maximum time (ms) between enqueue and commit.
    """

    def __init__(
        self,
        get_conn,
        lock: threading.Lock,
        *,
        batch_size: int = 50,
        flush_interval_ms: float = 100.0,
    ):
        self._get_conn = get_conn
        self._lock = lock
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_ms / 1000.0
        self._queue: list[_PendingWrite] = []
        self._queue_lock = threading.Lock()
        self._last_enqueue: float = 0.0
        self._timer: threading.Timer | None = None
        self._stats = {"enqueued": 0, "flushed": 0, "flush_count": 0}

    # ── Enqueue ───────────────────────────────────────────────

    def enqueue(self, sql: str, params: tuple = ()) -> None:
        """
        Add a single write to the batch queue.

        Triggers an immediate flush if the queue reaches ``batch_size``.
        Otherwise, starts (or resets) a timer for ``flush_interval_ms``.
        """
        with self._queue_lock:
            self._queue.append(_PendingWrite(sql=sql, params=params))
            self._stats["enqueued"] += 1
            self._last_enqueue = time.monotonic()

            if len(self._queue) >= self._batch_size:
                self._flush_unsafe()
            else:
                self._schedule_timer()

    def enqueue_many(self, sql: str, seq_params: list[tuple]) -> None:
        """
        Add a batch of writes sharing the same SQL statement.

        These will be committed together using ``executemany``.
        """
        if not seq_params:
            return
        with self._queue_lock:
            self._queue.append(
                _PendingWrite(sql=sql, params=(), is_many=True, seq_params=seq_params)
            )
            self._stats["enqueued"] += len(seq_params)
            self._last_enqueue = time.monotonic()

            if len(self._queue) >= self._batch_size:
                self._flush_unsafe()
            else:
                self._schedule_timer()

    # ── Flush ─────────────────────────────────────────────────

    def flush(self) -> int:
        """
        Force-flush all queued writes.  Returns count of operations committed.
        """
        with self._queue_lock:
            return self._flush_unsafe()

    def _flush_unsafe(self) -> int:
        """Flush without acquiring the queue lock (caller holds it)."""
        if not self._queue:
            return 0

        batch = self._queue[:]
        self._queue.clear()
        self._cancel_timer()

        count = 0
        with self._lock:
            conn = self._get_conn()
            old_iso = conn.isolation_level
            try:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                for pw in batch:
                    if pw.is_many and pw.seq_params:
                        conn.executemany(pw.sql, pw.seq_params)
                        count += len(pw.seq_params)
                    else:
                        conn.execute(pw.sql, pw.params)
                        count += 1
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
            finally:
                conn.isolation_level = old_iso

        self._stats["flushed"] += count
        self._stats["flush_count"] += 1
        return count

    # ── Timer management ──────────────────────────────────────

    def _schedule_timer(self) -> None:
        """Schedule a flush after ``flush_interval_ms``."""
        self._cancel_timer()
        self._timer = threading.Timer(self._flush_interval_s, self._timer_callback)
        self._timer.daemon = True
        self._timer.start()

    def _timer_callback(self) -> None:
        """Called by the timer thread when flush interval expires."""
        with self._queue_lock:
            self._flush_unsafe()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # ── Stats ─────────────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Number of operations currently queued."""
        return len(self._queue)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        """Flush remaining writes and stop timers."""
        self.flush()
        self._cancel_timer()

    def __repr__(self) -> str:
        return (
            f"<WriteBatcher batch_size={self._batch_size} "
            f"interval={self._flush_interval_s * 1000:.0f}ms "
            f"pending={self.pending}>"
        )
