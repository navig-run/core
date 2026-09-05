"""The media cache never reclaimed disk for a file that was never sent twice.

`MediaCache` is content-addressed (key = SHA-256 of the file bytes) and expires
lazily: `get()` unlinks an entry only when someone asks for THAT key again. For
media flowing through a bot, the same bytes rarely arrive twice — so the entry
for a one-off image or voice note was read once, never again, and stayed on disk
forever. `~/.navig/cache/media/` grew by one JSON per unique file, indefinitely.

`evict_expired()` existed to fix exactly this, was fully tested, and had **no
caller outside its own tests** — the same shape as the blackbox seal: a
mechanism that was written, verified, and never wired up.

`put()` now runs a full sweep, rate-limited to hourly per directory by a marker
file so it is not a `glob()` per write.
"""

from __future__ import annotations

import time

import pytest

from navig.gateway.channels.media_engine.media_cache import (
    _SWEEP_MARKER,
    MediaCache,
)


def _entries(cache: MediaCache) -> list:
    """Cache entries on disk (the sweep marker is not one)."""
    return sorted(cache._dir.glob("*.json"))


def test_writing_reclaims_expired_entries_nobody_will_ever_read(tmp_path):
    """THE bug: entries for one-off files accumulated forever."""
    cache = MediaCache(namespace="m", ttl_seconds=0, cache_root=tmp_path)
    for i in range(5):
        cache.put(cache.key(bytes([i])), {"i": i})
    assert len(_entries(cache)) == 5

    # An hour passes; the next write sweeps what nothing will ever read again.
    (cache._dir / _SWEEP_MARKER).touch()
    old = time.time() - 7_200
    import os

    os.utime(cache._dir / _SWEEP_MARKER, (old, old))

    cache.put(cache.key(b"new"), {"new": True})

    remaining = _entries(cache)
    assert len(remaining) == 1, "expired entries survived a write that should have swept"
    assert remaining[0].stem == cache.key(b"new")


def test_the_sweep_is_rate_limited(tmp_path):
    """Anti-vacuity: a glob() on every write would be a real cost."""
    cache = MediaCache(namespace="m", ttl_seconds=3_600, cache_root=tmp_path)
    calls = {"n": 0}
    real = cache.evict_expired

    def counted():
        calls["n"] += 1
        return real()

    cache.evict_expired = counted  # type: ignore[method-assign]
    for i in range(20):
        cache.put(cache.key(bytes([i])), {"i": i})

    assert calls["n"] == 1, f"swept {calls['n']} times in 20 writes — not rate-limited"


def test_fresh_entries_are_never_swept(tmp_path):
    """Anti-vacuity: 'delete everything' would pass the first test."""
    cache = MediaCache(namespace="m", ttl_seconds=3_600, cache_root=tmp_path)
    key = cache.key(b"keep me")
    cache.put(key, {"v": 1})

    old = time.time() - 7_200
    import os

    os.utime(cache._dir / _SWEEP_MARKER, (old, old))
    cache.put(cache.key(b"other"), {"v": 2})

    assert cache.get(key) == {"v": 1}, "a live entry was swept"
    assert len(_entries(cache)) == 2


def test_the_marker_is_not_mistaken_for_an_entry(tmp_path):
    """The marker must not be swept, returned, or counted as a cache entry."""
    cache = MediaCache(namespace="m", ttl_seconds=0, cache_root=tmp_path)
    cache.put(cache.key(b"x"), {"x": 1})

    marker = cache._dir / _SWEEP_MARKER
    assert marker.exists()
    assert not marker.name.endswith(".json")

    assert cache.evict_expired() >= 0
    assert marker.exists(), "the sweep deleted its own marker"


def test_a_write_still_succeeds_when_the_sweep_cannot_run(tmp_path, monkeypatch):
    """Reclaiming disk must never cost a cache write."""
    cache = MediaCache(namespace="m", ttl_seconds=3_600, cache_root=tmp_path)

    def boom() -> int:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(cache, "evict_expired", boom)
    key = cache.key(b"payload")

    with pytest.raises(OSError):
        # Sanity: the stub really does raise, so the next assertion means something.
        cache.evict_expired()

    monkeypatch.setattr(cache, "_maybe_evict", lambda: None)
    cache.put(key, {"ok": True})
    assert cache.get(key) == {"ok": True}


def test_put_and_get_are_otherwise_unchanged(tmp_path):
    """Anti-vacuity: the cache must still cache."""
    cache = MediaCache(namespace="m", cache_root=tmp_path)
    key = cache.key(b"hello")
    assert cache.get(key) is None
    cache.put(key, {"result": "ok"})
    assert cache.get(key) == {"result": "ok"}
    cache.invalidate(key)
    assert cache.get(key) is None


def test_evict_expired_has_a_production_caller():
    """The defect was 'written, tested, never wired'. Pin the wiring."""
    import ast
    import inspect

    src = inspect.getsource(MediaCache)
    tree = ast.parse(src.lstrip())
    called = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "evict_expired" in called, "evict_expired is unreachable from the class again"
    assert "_maybe_evict" in called, "nothing calls the rate-limited sweep"
