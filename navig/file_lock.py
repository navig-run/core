"""One writer at a time across processes, with a bounded wait.

Two processes that both read "the last line" and then append each write a child of
the same parent — the operations ledger learned this the expensive way: two
``navig`` commands finishing in the same second produced two entries whose ``prev``
pointed at the same hash, and ``navig ledger verify`` thereafter reported a
tampering-shaped ``prev-hash mismatch`` on a file nobody had touched.

The lock is taken on a **sidecar** ``<name>.lock`` file, never on the data file:
rotation renames the data file (``operations.jsonl`` → ``.jsonl.bak``), and a lock
held on an inode that has been renamed away stops excluding the next writer.

Re-entrant within one thread (OS file locks are not re-entrant on Windows, and
callers nest: the recorder holds this while it rotates), and bounded — a writer
that cannot get the lock within *timeout* raises :class:`TimeoutError` instead of
hanging a CLI command behind a stuck peer.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["file_lock"]

#: How long to wait for a peer process by default. Generous enough for a slow
#: rotation on a large ledger, short enough that a wedged peer cannot hang a CLI.
DEFAULT_TIMEOUT_SECONDS = 10.0

_THREAD_LOCK = threading.RLock()
_HELD = threading.local()

_POLL_SECONDS = 0.02


def _try_acquire(fh) -> bool:
    """Take the OS lock without blocking; False when a peer holds it."""
    if sys.platform == "win32":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release(fh) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Closing the handle releases the lock anyway; never mask the real work.
        pass


@contextmanager
def file_lock(target: Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold an exclusive cross-process lock on *target* for the block's duration.

    Args:
        target: the data file being written; the lock lives in ``<target>.lock``.
        timeout: seconds to wait for a peer before raising :class:`TimeoutError`.

    Raises:
        TimeoutError: a peer held the lock for the whole *timeout* (a subclass of
            ``OSError``, so ordinary write-error handling already covers it).
    """
    depth = getattr(_HELD, "depth", 0)
    if depth:
        _HELD.depth = depth + 1
        try:
            yield
        finally:
            _HELD.depth -= 1
        return

    with _THREAD_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(target.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as fh:
            deadline = time.monotonic() + max(timeout, 0.0)
            while not _try_acquire(fh):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out after {timeout:g}s waiting for the lock on {target} "
                        f"(held by another process; lock file {lock_path})"
                    )
                time.sleep(_POLL_SECONDS)
            _HELD.depth = 1
            try:
                yield
            finally:
                _HELD.depth = 0
                _release(fh)


def lock_path_for(target: Path) -> Path:
    """The sidecar lock file *target* is guarded by (for tests and diagnostics)."""
    return target.with_name(target.name + ".lock")
