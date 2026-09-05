"""Regression tests for WriteBatcher's failure + durability semantics.

Two bugs this guards against:
  * a failed commit used to DISCARD the whole batch (the queue was cleared before
    the transaction), silently losing already-accepted writes;
  * the flush timer reset on every enqueue, so steady sub-``batch_size`` traffic
    kept pushing the deadline back and never flushed within ``flush_interval_ms``.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from navig.storage.write_batcher import WriteBatcher


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "wb.db"))
    conn.execute("CREATE TABLE items (v INTEGER)")
    conn.commit()
    return conn


def test_failed_flush_preserves_the_batch(tmp_path):
    """A commit failure must NOT discard already-accepted writes: the batch stays
    queued for retry. Before the fix the queue was cleared before the transaction,
    so the raise dropped the whole batch (pending → 0)."""
    conn = _conn(tmp_path)
    b = WriteBatcher(lambda: conn, threading.Lock(), batch_size=2, flush_interval_ms=10_000)

    b.enqueue("INSERT INTO items VALUES (?)", (1,))  # valid — pending 1
    with pytest.raises(sqlite3.OperationalError):
        # bad table → hits batch_size → _flush_unsafe → transaction raises
        b.enqueue("INSERT INTO missing VALUES (?)", (2,))

    assert b.pending == 2  # batch preserved (pre-fix: 0 — both writes lost)

    # Recovery: once the failing condition clears, the preserved batch flushes and
    # BOTH rows land — nothing was lost.
    conn.execute("CREATE TABLE missing (v INTEGER)")
    conn.commit()
    assert b.flush() == 2
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM missing").fetchone()[0] == 1
    b.close()


def test_successful_flush_commits_and_clears(tmp_path):
    """Happy path is unchanged: a manual flush commits and empties the queue."""
    conn = _conn(tmp_path)
    b = WriteBatcher(lambda: conn, threading.Lock(), batch_size=50, flush_interval_ms=10_000)
    b.enqueue("INSERT INTO items VALUES (?)", (1,))
    b.enqueue("INSERT INTO items VALUES (?)", (2,))
    assert b.pending == 2
    assert b.flush() == 2
    assert b.pending == 0
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    b.close()


def test_batch_size_triggers_auto_flush(tmp_path):
    """Reaching batch_size flushes immediately (count trigger)."""
    conn = _conn(tmp_path)
    b = WriteBatcher(lambda: conn, threading.Lock(), batch_size=2, flush_interval_ms=10_000)
    b.enqueue("INSERT INTO items VALUES (?)", (1,))
    b.enqueue("INSERT INTO items VALUES (?)", (2,))  # 2 == batch_size → auto-flush
    assert b.pending == 0
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    b.close()


def test_schedule_timer_measures_from_first_enqueue(tmp_path):
    """The flush timer must not reset on every enqueue — otherwise steady sub-batch
    traffic keeps pushing the deadline back and never flushes. A second schedule
    while a timer is already pending must REUSE it, not replace it."""
    b = WriteBatcher(lambda: None, threading.Lock(), batch_size=50, flush_interval_ms=10_000)
    b._schedule_timer()
    t1 = b._timer
    b._schedule_timer()  # what a second sub-batch enqueue would do
    t2 = b._timer
    b._cancel_timer()  # cancel before asserting — no lingering daemon timer
    assert t1 is not None
    assert t1 is t2  # same timer (pre-fix created a fresh one each call)


def test_timer_flush_failure_is_caught_and_rearmed(tmp_path):
    """The timer thread has no caller to receive an exception. A failed scheduled
    flush must NOT escape (pre-fix: uncaught traceback crashing the daemon thread) and
    must RE-ARM a retry timer — otherwise the preserved batch is stranded in memory
    until the next enqueue/close and lost if the process exits quietly first."""
    conn = _conn(tmp_path)
    b = WriteBatcher(lambda: conn, threading.Lock(), batch_size=50, flush_interval_ms=10_000)
    b.enqueue("INSERT INTO missing VALUES (?)", (1,))  # bad table, sub-batch → queued
    assert b.pending == 1

    b._timer_callback()  # simulate the timer firing — pre-fix this RAISED

    assert b.pending == 1          # batch preserved (not lost)
    assert b._timer is not None    # re-armed so the write actually retries
    b._cancel_timer()              # stop the retry timer before recovery

    # Recovery: once the condition clears, the held write flushes and lands.
    conn.execute("CREATE TABLE missing (v INTEGER)")
    conn.commit()
    assert b.flush() == 1
    assert conn.execute("SELECT COUNT(*) FROM missing").fetchone()[0] == 1
    b.close()


def test_retry_backoff_escalates_then_resets_on_success(tmp_path):
    """Repeated timer-flush failures escalate the backoff (so a stuck DB doesn't
    hot-spin), and a later successful flush resets it to zero."""
    conn = _conn(tmp_path)
    b = WriteBatcher(lambda: conn, threading.Lock(), batch_size=50, flush_interval_ms=10_000)
    b.enqueue("INSERT INTO missing VALUES (?)", (1,))

    b._timer_callback()
    first = b._retry_failures
    b._cancel_timer()
    b._timer_callback()
    second = b._retry_failures
    b._cancel_timer()
    assert first >= 1 and second > first  # backoff escalates on repeated failure

    conn.execute("CREATE TABLE missing (v INTEGER)")
    conn.commit()
    b.flush()  # success
    assert b._retry_failures == 0  # reset once the DB is writable again
    b.close()
