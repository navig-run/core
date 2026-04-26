"""Tests for navig.mesh.dedup — DeduplicationFilter and HandoffQueue."""
from __future__ import annotations

import asyncio
import time

import pytest

from navig.mesh.dedup import DeduplicationFilter, HandoffQueue


# ---------------------------------------------------------------------------
# DeduplicationFilter
# ---------------------------------------------------------------------------

class TestDeduplicationFilter:
    def test_not_duplicate_first_time(self):
        f = DeduplicationFilter()
        assert f.is_duplicate(1, 100) is False

    def test_duplicate_after_mark_seen(self):
        f = DeduplicationFilter()
        f.mark_seen(1, 100)
        assert f.is_duplicate(1, 100) is True

    def test_different_message_not_duplicate(self):
        f = DeduplicationFilter()
        f.mark_seen(1, 100)
        assert f.is_duplicate(2, 100) is False

    def test_different_chat_not_duplicate(self):
        f = DeduplicationFilter()
        f.mark_seen(1, 100)
        assert f.is_duplicate(1, 200) is False

    def test_len_increments_on_mark_seen(self):
        f = DeduplicationFilter()
        assert len(f) == 0
        f.mark_seen(1, 100)
        f.mark_seen(2, 100)
        assert len(f) == 2

    def test_mark_seen_idempotent(self):
        f = DeduplicationFilter()
        f.mark_seen(1, 100)
        f.mark_seen(1, 100)
        assert len(f) == 1

    def test_eviction_removes_stale_entries(self):
        f = DeduplicationFilter(window_seconds=1)
        f.mark_seen(1, 100)
        assert len(f) == 1
        # Fake the timestamp to be stale
        key = list(f._seen.keys())[0]
        f._seen[key] = time.monotonic() - 2  # 2s ago > 1s window
        f._evict()
        assert len(f) == 0

    def test_evicted_entry_not_duplicate(self):
        f = DeduplicationFilter(window_seconds=1)
        f.mark_seen(1, 100)
        key = list(f._seen.keys())[0]
        f._seen[key] = time.monotonic() - 5
        assert f.is_duplicate(1, 100) is False  # evicted → not duplicate

    def test_key_deterministic(self):
        k1 = DeduplicationFilter._key(42, 99)
        k2 = DeduplicationFilter._key(42, 99)
        assert k1 == k2
        assert len(k1) == 16

    def test_key_differs_for_different_inputs(self):
        k1 = DeduplicationFilter._key(1, 1)
        k2 = DeduplicationFilter._key(1, 2)
        assert k1 != k2


# ---------------------------------------------------------------------------
# HandoffQueue
# ---------------------------------------------------------------------------

class TestHandoffQueue:
    def test_empty_on_init(self):
        q = HandoffQueue()
        assert q.is_empty() is True
        assert len(q) == 0

    def test_put_adds_item(self):
        q = HandoffQueue()
        q.put({"msg": "hello"})
        assert len(q) == 1
        assert q.is_empty() is False

    def test_put_multiple(self):
        q = HandoffQueue()
        q.put("a")
        q.put("b")
        q.put("c")
        assert len(q) == 3

    def test_drain_delivers_messages(self):
        q = HandoffQueue(default_ttl_s=60)
        q.put("msg1")
        q.put("msg2")
        delivered_items = []

        async def send_fn(msg):
            delivered_items.append(msg)

        delivered, dropped = asyncio.run(q.drain_to_leader(send_fn))
        assert delivered == 2
        assert dropped == 0
        assert delivered_items == ["msg1", "msg2"]
        assert q.is_empty()

    def test_drain_drops_expired_items(self):
        q = HandoffQueue(default_ttl_s=60)
        q.put("stale", ttl_seconds=1)
        # Backdate the deadline to simulate expiry
        q._queue[0] = (q._queue[0][0], q._queue[0][1], time.monotonic() - 5)

        received = []

        async def send_fn(msg):
            received.append(msg)

        delivered, dropped = asyncio.run(
            q.drain_to_leader(send_fn)
        )
        assert delivered == 0
        assert dropped == 1
        assert received == []

    def test_drain_handles_send_fn_exception(self):
        q = HandoffQueue()
        q.put("bad")

        async def send_fn(msg):
            raise RuntimeError("delivery failed")

        delivered, dropped = asyncio.run(
            q.drain_to_leader(send_fn)
        )
        assert delivered == 0
        assert dropped == 1

    def test_drain_clears_queue(self):
        q = HandoffQueue()
        q.put("x")

        async def noop(msg):
            pass

        asyncio.run(q.drain_to_leader(noop))
        assert q.is_empty()

    def test_custom_ttl_per_item(self):
        q = HandoffQueue(default_ttl_s=5)
        q.put("override", ttl_seconds=120)
        _, _, deadline = q._queue[0]
        assert deadline > time.monotonic() + 100  # ~120s from now
