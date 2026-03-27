"""
QueryTimer — Per-query latency tracking with p50/p95/p99 percentiles.

Captures wall-clock time for every query executed through the engine.
Optionally logs slow queries (above configurable threshold) to the
audit store.

Thread-safe: uses a lock around the stats arrays.  Low overhead —
appends to a fixed-size ring buffer (default 10 000 samples).

Usage::

    timer = QueryTimer(slow_threshold_ms=20.0)

    with timer.track("SELECT * FROM chunks WHERE id = ?"):
        cursor.execute(...)

    stats = timer.get_stats()
    # {"count": 42, "p50_ms": 0.3, "p95_ms": 1.2, "p99_ms": 4.8, ...}
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# Max samples kept per query label for percentile calculation.
_MAX_SAMPLES = 10_000


@dataclass
class QueryStats:
    """Aggregated statistics for a single query label."""

    label: str
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    # Sorted samples for percentile calculation (capped at _MAX_SAMPLES)
    _samples: List[float] = field(default_factory=list, repr=False)

    def record(self, duration_ms: float) -> None:
        self.count += 1
        self.total_ms += duration_ms
        self.min_ms = min(self.min_ms, duration_ms)
        self.max_ms = max(self.max_ms, duration_ms)
        # Maintain sorted order with bisect for O(log n) insert
        if len(self._samples) < _MAX_SAMPLES:
            bisect.insort(self._samples, duration_ms)
        else:
            # Reservoir sampling: replace a random element
            # For simplicity, evict the oldest (front) if new value is relevant
            if duration_ms > self._samples[0]:
                self._samples.pop(0)
                bisect.insort(self._samples, duration_ms)

    def percentile(self, p: float) -> float:
        """Return the p-th percentile (0..100) from collected samples."""
        if not self._samples:
            return 0.0
        idx = int(len(self._samples) * p / 100)
        idx = min(idx, len(self._samples) - 1)
        return self._samples[idx]

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
            "avg_ms": round(self.avg_ms, 3),
            "min_ms": round(self.min_ms, 3) if self.min_ms != float("inf") else 0.0,
            "max_ms": round(self.max_ms, 3),
            "p50_ms": round(self.percentile(50), 3),
            "p95_ms": round(self.percentile(95), 3),
            "p99_ms": round(self.percentile(99), 3),
        }


class QueryTimer:
    """
    Global query latency tracker.

    Parameters
    ----------
    slow_threshold_ms : float
        Queries exceeding this wall-clock time are logged as warnings.
        Set to 0 to disable slow-query logging.
    log_to_audit : bool
        If True, slow queries are also written to the audit store
        (best-effort, never blocks the caller).
    """

    def __init__(
        self,
        *,
        slow_threshold_ms: float = 20.0,
        log_to_audit: bool = False,
    ):
        self.slow_threshold_ms = slow_threshold_ms
        self.log_to_audit = log_to_audit
        self._stats: Dict[str, QueryStats] = defaultdict(lambda: QueryStats(label=""))
        self._lock = threading.Lock()

    # ── Context manager ───────────────────────────────────────

    @contextmanager
    def track(self, label: str) -> Generator[None, None, None]:
        """
        Time a block of code and record it under *label*.

        Usage::

            with timer.track("audit.query_events"):
                rows = conn.execute(sql, params).fetchall()
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._record(label, elapsed_ms)

    def time_call(self, label: str):
        """
        Decorator variant of ``track()``.

        Usage::

            @timer.time_call("audit.get_stats")
            def get_stats(self):
                ...
        """

        def decorator(fn):
            def wrapper(*args, **kwargs):
                with self.track(label):
                    return fn(*args, **kwargs)

            wrapper.__name__ = fn.__name__
            wrapper.__doc__ = fn.__doc__
            return wrapper

        return decorator

    # ── Recording ─────────────────────────────────────────────

    def _record(self, label: str, elapsed_ms: float) -> None:
        with self._lock:
            qs = self._stats[label]
            if not qs.label:
                qs.label = label
            qs.record(elapsed_ms)

        if self.slow_threshold_ms > 0 and elapsed_ms > self.slow_threshold_ms:
            logger.warning("Slow query [%.1fms] %s", elapsed_ms, label)
            if self.log_to_audit:
                self._log_slow_to_audit(label, elapsed_ms)

    def _log_slow_to_audit(self, label: str, elapsed_ms: float) -> None:
        """Best-effort write to audit store."""
        try:
            from navig.store.audit import get_audit_store

            get_audit_store().log_event(
                action="query.slow",
                actor="engine",
                target=label,
                details={"elapsed_ms": round(elapsed_ms, 2)},
                status="warning",
                duration_ms=int(elapsed_ms),
            )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    # ── Stats retrieval ───────────────────────────────────────

    def get_stats(self, label: Optional[str] = None) -> Dict[str, Any]:
        """
        Return latency statistics.

        If *label* is given, return stats for that query only.
        Otherwise return all tracked queries.
        """
        with self._lock:
            if label:
                qs = self._stats.get(label)
                return qs.to_dict() if qs else {}
            return {k: v.to_dict() for k, v in self._stats.items()}

    def get_slow_queries(
        self, threshold_ms: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Return stats for queries whose p95 exceeds *threshold_ms*."""
        threshold = threshold_ms or self.slow_threshold_ms
        with self._lock:
            return [
                v.to_dict()
                for v in self._stats.values()
                if v.percentile(95) > threshold
            ]

    def reset(self) -> None:
        """Clear all recorded statistics."""
        with self._lock:
            self._stats.clear()


# ── Module-level singleton ────────────────────────────────────

_timer: Optional[QueryTimer] = None


def get_query_timer(**kwargs) -> QueryTimer:
    """Get or create the global QueryTimer instance."""
    global _timer
    if _timer is None:
        _timer = QueryTimer(**kwargs)
    return _timer
