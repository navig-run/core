"""Tests for navig.gateway.channels.media_engine.media_cache — MediaCache."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.gateway.channels.media_engine.media_cache import MediaCache


@pytest.fixture
def cache(tmp_path):
    return MediaCache(namespace="test_media", cache_root=tmp_path)


class TestMediaCacheKey:
    def test_returns_sha256_hex(self, cache):
        import hashlib
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert cache.key(data) == expected

    def test_deterministic(self, cache):
        data = b"deterministic"
        assert cache.key(data) == cache.key(data)

    def test_different_data_different_key(self, cache):
        assert cache.key(b"a") != cache.key(b"b")


class TestMediaCacheGetPut:
    def test_miss_returns_none(self, cache):
        assert cache.get("nonexistent" * 4) is None

    def test_put_then_get_roundtrip(self, cache):
        data = b"file content"
        key = cache.key(data)
        cache.put(key, {"label": "photo", "size": 1024})
        result = cache.get(key)
        assert result is not None
        assert result["label"] == "photo"

    def test_expired_entry_returns_none(self, tmp_path):
        short_cache = MediaCache(namespace="short", ttl_seconds=0, cache_root=tmp_path)
        data = b"something"
        key = short_cache.key(data)
        short_cache.put(key, {"ok": True})
        # With ttl=0, any age is expired
        result = short_cache.get(key)
        assert result is None

    def test_expired_entry_deleted(self, tmp_path):
        short_cache = MediaCache(namespace="short2", ttl_seconds=0, cache_root=tmp_path)
        data = b"data"
        key = short_cache.key(data)
        short_cache.put(key, {"x": 1})
        short_cache.get(key)  # triggers deletion
        cache_file = tmp_path / "short2" / f"{key}.json"
        assert not cache_file.exists()

    def test_get_returns_none_on_corrupt_file(self, tmp_path):
        cache = MediaCache(namespace="corrupt", cache_root=tmp_path)
        key = "a" * 64
        path = tmp_path / "corrupt" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not json")
        result = cache.get(key)
        assert result is None

    def test_put_does_not_raise_on_error(self, monkeypatch, cache):
        # Monkeypatch atomic_write_text to raise
        import navig.gateway.channels.media_engine.media_cache as mc_mod
        monkeypatch.setattr(mc_mod, "atomic_write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        key = cache.key(b"data")
        cache.put(key, {"val": 1})  # must not raise


class TestMediaCacheInvalidate:
    def test_invalidate_removes_entry(self, cache):
        data = b"to remove"
        key = cache.key(data)
        cache.put(key, {"delete": True})
        cache.invalidate(key)
        assert cache.get(key) is None

    def test_invalidate_nonexistent_is_noop(self, cache):
        cache.invalidate("z" * 64)  # no error


class TestMediaCacheEvict:
    def test_evict_expired_removes_old_files(self, tmp_path):
        short_cache = MediaCache(namespace="evict", ttl_seconds=0, cache_root=tmp_path)
        for i in range(3):
            key = short_cache.key(bytes([i]))
            short_cache.put(key, {"i": i})
        removed = short_cache.evict_expired()
        assert removed == 3

    def test_evict_returns_zero_on_empty_cache(self, cache):
        assert cache.evict_expired() == 0

    def test_evict_keeps_fresh_entries(self, tmp_path):
        fresh_cache = MediaCache(namespace="fresh", ttl_seconds=3600, cache_root=tmp_path)
        key = fresh_cache.key(b"fresh")
        fresh_cache.put(key, {"fresh": True})
        removed = fresh_cache.evict_expired()
        assert removed == 0
        assert fresh_cache.get(key) is not None
