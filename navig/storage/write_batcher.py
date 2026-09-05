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

    # Cap for the timer-flush retry backoff (see ``_schedule_retry``). A persistently
    # locked DB retries at most this often rather than hot-spinning every interval.
    _MAX_RETRY_S = 5.0

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
        self._timer: threading.Timer | None = None
        self._retry_failures = 0  # consecutive timer-flush failures (drives backoff)
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

        # Copy — but DON'T clear yet. If the commit fails, the batch must stay in
        # the queue: clearing up-front dropped already-accepted writes on any
        # transaction error (locked DB, bad SQL, disk full). The caller holds
        # _queue_lock, so nothing is appended during the flush and the batch stays
        # the queue head; we remove exactly those entries only after COMMIT.
        batch = self._queue[:]
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
                    pass  # best-effort: DB lock or unavailable; skip
                raise  # batch is preserved in the queue for a later retry
            finally:
                conn.isolation_level = old_iso

        del self._queue[: len(batch)]  # commit succeeded → drop the flushed entries
        self._retry_failures = 0  # DB is writable again → reset the retry backoff
        self._stats["flushed"] += count
        self._stats["flush_count"] += 1
        return count

    # ── Timer management ──────────────────────────────────────

    def _schedule_timer(self) -> None:
        """Arm a flush timer, but only if one is not already pending.

        Arming only when idle means the deadline is measured from the FIRST
        unflushed enqueue, not the last. Resetting it on every enqueue (the old
        behaviour) let steady sub-``batch_size`` traffic keep pushing the deadline
        back, so the queue never flushed on time and writes were stranded in memory —
        breaking the ``flush_interval_ms`` durability guarantee.
        """
        if self._timer is not None:
            return
        self._timer = threading.Timer(self._flush_interval_s, self._timer_callback)
        self._timer.daemon = True
        self._timer.start()

    def _timer_callback(self) -> None:
        """Called by the timer thread when the flush interval expires.

        This runs in a daemon ``threading.Timer`` thread that has NO caller to receive
        an exception. ``_flush_unsafe`` deliberately RE-RAISES on a commit failure to
        preserve the batch for retry — but if that propagated here it would (1) crash
        the timer thread with an uncaught traceback and (2) leave no timer armed
        (``_flush_unsafe`` clears it before failing), stranding the preserved writes in
        memory until the next ``enqueue`` or ``close`` — and losing them outright if the
        process exits quietly first. So catch, log, and re-arm a retry timer here. A
        successful flush clears the queue and resets the backoff (in ``_flush_unsafe``).
        """
        with self._queue_lock:
            try:
                self._flush_unsafe()
            except Exception:
                pending = len(self._queue)
                logger.warning(
                    "WriteBatcher: scheduled flush failed; holding %d op(s) for retry",
                    pending,
                    exc_info=True,
                )
                if pending:
                    self._schedule_retry()

    def _schedule_retry(self) -> None:
        """Re-arm the flush timer after a failed timer flush, with capped exponential
        backoff so a persistently-locked DB retries (durably) without hot-spinning or
        spamming the log every interval. Caller holds ``_queue_lock``; ``_flush_unsafe``
        has already cleared ``_timer``, so this is the sole armer on the failure path."""
        self._retry_failures += 1
        delay = min(self._flush_interval_s * (2 ** self._retry_failures), self._MAX_RETRY_S)
        self._timer = threading.Timer(delay, self._timer_callback)
        self._timer.daemon = True
        self._timer.start()

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
