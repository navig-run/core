"""
Tests for navig.mesh.dedup — DeduplicationFilter and HandoffQueue.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_dedup_import():
    from navig.mesh.dedup import DeduplicationFilter, HandoffQueue

    assert DeduplicationFilter is not None
    assert HandoffQueue is not None


# ===========================================================================
# DeduplicationFilter
# ===========================================================================


class TestDeduplicationFilter:
    def _filter(self, window=300):
        from navig.mesh.dedup import DeduplicationFilter

        return DeduplicationFilter(window_seconds=window)

    def test_first_message_not_duplicate(self):
        f = self._filter()
        assert f.is_duplicate(1001, 42) is False

    def test_second_occurrence_is_duplicate(self):
        f = self._filter()
        f.mark_seen(1001, 42)
        assert f.is_duplicate(1001, 42) is True

    def test_different_chat_not_duplicate(self):
        f = self._filter()
        f.mark_seen(1001, 42)
        assert f.is_duplicate(1001, 99) is False

    def test_different_message_id_not_duplicate(self):
        f = self._filter()
        f.mark_seen(1001, 42)
        assert f.is_duplicate(1002, 42) is False

    def test_mark_seen_then_check(self):
        f = self._filter()
        f.mark_seen(555, 10)
        assert f.is_duplicate(555, 10) is True

    def test_expired_entry_is_not_duplicate(self):
        f = self._filter(window=1)
        f.mark_seen(777, 10)
        # Manually age the entry
        key = list(f._seen.keys())[0]
        f._seen[key] = time.monotonic() - 2  # 2s ago, window is 1s
        assert f.is_duplicate(777, 10) is False

    def test_len_reflects_seen_count(self):
        f = self._filter()
        assert len(f) == 0
        f.mark_seen(1, 1)
        f.mark_seen(2, 1)
        assert len(f) == 2

    def test_double_mark_seen_does_not_duplicate_entry(self):
        f = self._filter()
        f.mark_seen(1, 1)
        f.mark_seen(1, 1)
        assert len(f) == 1

    def test_evict_clears_expired_entries(self):
        f = self._filter(window=1)
        f.mark_seen(1, 1)
        f.mark_seen(2, 1)
        # Age all entries
        for k in f._seen:
            f._seen[k] = time.monotonic() - 2
        f._evict()
        assert len(f) == 0


# ===========================================================================
# HandoffQueue
# ===========================================================================


class TestHandoffQueue:
    def _queue(self, ttl=60):
        from navig.mesh.dedup import HandoffQueue

        return HandoffQueue(default_ttl_s=ttl)

    def test_empty_on_creation(self):
        q = self._queue()
        assert q.is_empty()
        assert len(q) == 0

    def test_put_increases_length(self):
        q = self._queue()
        q.put({"update_id": 1})
        assert len(q) == 1

    def test_is_empty_false_after_put(self):
        q = self._queue()
        q.put({})
        assert not q.is_empty()

    @pytest.mark.asyncio
    async def test_drain_delivers_messages(self):
        q = self._queue()
        delivered = []

        async def fake_send(msg):
            delivered.append(msg)

        q.put({"update_id": 10})
        q.put({"update_id": 11})

        ok, dropped = await q.drain_to_leader(fake_send)
        assert ok == 2
        assert dropped == 0
        assert len(delivered) == 2
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_drain_drops_expired_messages(self):
        q = self._queue(ttl=1)
        q.put({"update_id": 20})
        # Age the message past TTL
        item = q._queue[0]
        q._queue[0] = (item[0], item[1], time.monotonic() - 0.1)  # already expired

        dropped_list = []

        async def fake_send(msg):
            pass

        ok, dropped = await q.drain_to_leader(fake_send)
        assert dropped == 1
        assert ok == 0

    @pytest.mark.asyncio
    async def test_drain_clears_queue(self):
        q = self._queue()
        q.put({})
        q.put({})
        await q.drain_to_leader(AsyncMock())
        assert q.is_empty()

    @pytest.mark.asyncio
    async def test_drain_tolerates_send_error(self):
        q = self._queue()
        q.put({"update_id": 99})

        async def failing_send(msg):
            raise RuntimeError("network error")

        ok, dropped = await q.drain_to_leader(failing_send)
        assert dropped == 1
        assert ok == 0
