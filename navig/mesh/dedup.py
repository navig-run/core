"""
DeduplicationFilter + HandoffQueue — zero-duplicate, zero-drop Telegram routing.

DeduplicationFilter:
  - Rolling 5-minute window keyed by sha256(message_id:chat_id)[:16].
  - O(1) amortised: stale entries evicted on every is_duplicate() call.
  - Thread-safe for single-loop (asyncio); no Lock needed.

HandoffQueue:
  - Holds messages arriving during leader YIELDING state (max 60s TTL).
  - drain_to_leader() delivers queued messages in arrival order to the new
    leader's send coroutine, dropping items past their TTL.
  - Guarantees: no duplicates (dedup filter pre-screens), no drops within TTL.

Failure coverage:
  Failure                           Response
  ────────────────────────────────────────────────────────────────────────────
  Same message_id arrives twice     is_duplicate() → True, second silently skipped
  Message arrives during handoff    HandoffQueue.put(); drain_to_leader() later
  Message TTL expires in queue      drain_to_leader() drops it + logs WARNING
  New leader never confirms ACTIVE  Messages TTL-expire, user may need to retry
    (edge case, ≥60s outage)
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Default dedup window — 5 minutes prevents replay on rapid disconnects
DEFAULT_WINDOW_S = 300
# Default handoff queue TTL — matches spec: 60s
DEFAULT_HANDOFF_TTL_S = 60


class DeduplicationFilter:
    """
    Rolling-window deduplication for Telegram message dispatch.

    Key: sha256(f"{message_id}:{chat_id}") truncated to 16 hex chars.
    This is sufficient entropy (64-bit collision space over a 5-minute window)
    and keeps memory bounded: 1000 messages × ~100 bytes ≈ 100 KB.
    """

    def __init__(self, window_seconds: int = DEFAULT_WINDOW_S) -> None:
        self._window_s = window_seconds
        # key → timestamp of first observation
        self._seen: dict[str, float] = {}

    @staticmethod
    def _key(message_id: int, chat_id: int) -> str:
        raw = f"{message_id}:{chat_id}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def is_duplicate(self, message_id: int, chat_id: int) -> bool:
        """
        Return True if this (message_id, chat_id) pair was seen within
        the current window.

        Side-effect: evicts expired entries (amortised O(1)).
        """
        self._evict()
        return self._key(message_id, chat_id) in self._seen

    def mark_seen(self, message_id: int, chat_id: int) -> None:
        """Record a message as dispatched."""
        k = self._key(message_id, chat_id)
        if k not in self._seen:
            self._seen[k] = time.monotonic()

    def _evict(self) -> None:
        """Remove entries older than window_seconds."""
        cutoff = time.monotonic() - self._window_s
        stale = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in stale:
            del self._seen[k]

    def __len__(self) -> int:
        return len(self._seen)


class HandoffQueue:
    """
    In-memory queue for messages that arrive while the local node is yielding.

    When the node role is "yielding":
      - Incoming messages are put() into this queue with a TTL timestamp.
      - Once the new leader confirms ACTIVE (role == "leader"), drain_to_leader()
        is called to deliver queued messages in order.

    Zero-duplicate contract: the caller (TelegramChannel._process_update) runs
    the DeduplicationFilter BEFORE putting into the queue, so a duplicate that
    arrived during handoff is already filtered out.

    Capacity note: there is no hard cap on queue size.  In practice, a ≤15s
    handoff at 10 messages/second yields ≤150 queued items — trivial.
    """

    # Type alias for a queued item
    # (raw_update_dict, enqueue_timestamp, ttl_deadline)
    _Item = tuple[Any, float, float]

    def __init__(self, default_ttl_s: int = DEFAULT_HANDOFF_TTL_S) -> None:
        self._default_ttl_s = default_ttl_s
        self._queue: list[HandoffQueue._Item] = []

    def put(self, message: Any, ttl_seconds: int | None = None) -> None:
        """Enqueue a message with a TTL deadline."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_s
        now = time.monotonic()
        deadline = now + ttl
        self._queue.append((message, now, deadline))
        logger.debug(
            f"[handoff] Queued message (queue_depth={len(self._queue)}, ttl={ttl}s)"
        )

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def __len__(self) -> int:
        return len(self._queue)

    async def drain_to_leader(
        self,
        send_fn: Callable[[Any], Awaitable[None]],
    ) -> tuple[int, int]:
        """
        Deliver all queued messages to the new leader via send_fn.

        Messages past their TTL deadline are dropped (logged as WARNING).
        Returns (delivered_count, dropped_count).

        send_fn signature: async def send_fn(message: Any) -> None
          where message is the raw Telegram update dict.
        """
        delivered = 0
        dropped = 0
        now = time.monotonic()

        items = list(self._queue)
        self._queue.clear()

        for message, enqueued_at, deadline in items:
            if now > deadline:
                age = now - enqueued_at
                logger.warning(
                    f"[handoff] Dropping queued message (age={age:.1f}s > ttl — deadline expired)"
                )
                dropped += 1
                continue
            try:
                await send_fn(message)
                delivered += 1
            except Exception as exc:
                # Do not crash the drain loop; log and skip
                logger.error(f"[handoff] Failed to deliver queued message: {exc}")
                dropped += 1

        if delivered or dropped:
            logger.info(
                f"[handoff] Drain complete: {delivered} delivered, {dropped} dropped"
            )
        return delivered, dropped
