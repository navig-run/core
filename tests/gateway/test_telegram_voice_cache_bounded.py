"""`telegram_voice._af_cache` must be bounded.

The always-on Telegram bot wrote one metadata entry per audio message received and nothing
ever deleted them — a plain dict grew the daemon's RAM without limit. It's now a capped LRU;
this pins that so a refactor can't silently turn it back into an unbounded dict.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram_voice import _af_cache, _BoundedCache

pytestmark = pytest.mark.unit


def test_af_cache_is_a_capped_lru():
    _af_cache.clear()
    for i in range(_BoundedCache._MAX + 100):
        _af_cache[f"id{i}"] = {"file_id": f"f{i}"}

    # Hard cap — never grows past _MAX no matter how many messages arrive.
    assert len(_af_cache) == _BoundedCache._MAX
    # Oldest entries evicted, newest retained (LRU by insertion).
    assert _af_cache.get("id0") is None
    assert _af_cache.get(f"id{_BoundedCache._MAX + 99}") == {"file_id": f"f{_BoundedCache._MAX + 99}"}
    # The cross-module reader relies on the `.get(id, default)` interface — preserve it.
    assert _af_cache.get("does-not-exist", {}) == {}
    _af_cache.clear()


def test_af_cache_reinsert_refreshes_recency():
    _af_cache.clear()
    for i in range(_BoundedCache._MAX):
        _af_cache[f"id{i}"] = {"n": i}
    # Touch the oldest so it becomes most-recent, then overflow by one.
    _af_cache["id0"] = {"n": 0, "touched": True}
    _af_cache["overflow"] = {"n": -1}

    assert len(_af_cache) == _BoundedCache._MAX
    assert _af_cache.get("id0") == {"n": 0, "touched": True}  # survived (was refreshed)
    assert _af_cache.get("id1") is None  # id1 is now the oldest → evicted
    _af_cache.clear()
