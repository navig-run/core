"""
Transaction helpers — Context managers for SQLite transactions.

Provides:
    begin_immediate(conn)   Acquire RESERVED lock upfront to prevent SQLITE_BUSY
    savepoint(conn, name)   Partial rollback within a larger transaction
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator


@contextmanager
def begin_immediate(
    conn: sqlite3.Connection,
) -> Generator[sqlite3.Connection, None, None]:
    """
    BEGIN IMMEDIATE transaction.

    Acquires a RESERVED lock on entry, preventing other writers from
    interleaving.  Commits on clean exit, rolls back on exception.

    Usage::

        with begin_immediate(conn) as c:
            c.execute("INSERT INTO ...")
            c.execute("UPDATE ...")
        # auto-committed here

    Why IMMEDIATE:
        Default ``BEGIN`` defers lock acquisition until the first write,
        which can cause SQLITE_BUSY if another writer grabs the lock
        between ``BEGIN`` and the first ``INSERT``.  ``BEGIN IMMEDIATE``
        front-loads the lock acquisition and fails fast.
    """
    # Disable Python's sqlite3 auto-transaction (isolation_level=None
    # would do this globally; we do it locally instead).
    old_il = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass  # might already be rolled back
        raise
    finally:
        conn.isolation_level = old_il


@contextmanager
def savepoint(
    conn: sqlite3.Connection,
    name: str = "sp",
) -> Generator[sqlite3.Connection, None, None]:
    """
    SAVEPOINT inside an existing transaction.

    On clean exit: ``RELEASE SAVEPOINT`` (merge into parent tx).
    On exception: ``ROLLBACK TO SAVEPOINT`` (undo only this block).

    Usage::

        with begin_immediate(conn) as c:
            c.execute("INSERT INTO main_table ...")
            try:
                with savepoint(c, "audit") as sc:
                    sc.execute("INSERT INTO audit_events ...")
                    # if this fails, only the audit insert rolls back
            except Exception:
                pass  # audit failure is non-fatal
        # main_table insert still commits

    Savepoints are essential for multi-step operations where partial
    failure of a secondary write (audit, cache update) should not
    abort the primary write.
    """
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
        conn.execute(f"RELEASE SAVEPOINT {name}")
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            conn.execute(f"RELEASE SAVEPOINT {name}")
        except sqlite3.OperationalError:
            pass  # best-effort rollback; may already be clean
        raise
