"""Tests for navig.cache_store — JSON caching helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _to_iso_z
# ---------------------------------------------------------------------------


class TestToIsoZ:
    def _fn(self):
        from navig.cache_store import _to_iso_z

        return _to_iso_z

    def test_utc_datetime_ends_with_z(self):
        fn = self._fn()
        dt = _utc(datetime(2025, 1, 15, 12, 0, 0))
        result = fn(dt)
        assert result.endswith("Z")

    def test_naive_datetime_treated_as_utc(self):
        fn = self._fn()
        dt = datetime(2025, 1, 15, 12, 0, 0)  # naive
        result = fn(dt)
        assert result.endswith("Z")
        assert "2025-01-15" in result

    def test_non_utc_tz_converted(self):
        fn = self._fn()
        # UTC-5 fixed offset: 07:00 local = 12:00 UTC
        tz_minus5 = timezone(timedelta(hours=-5))
        dt = datetime(2025, 1, 15, 7, 0, 0, tzinfo=tz_minus5)
        result = fn(dt)
        assert result.endswith("Z")
        assert "12:00:00" in result


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def _fn(self):
        from navig.cache_store import _parse_iso

        return _parse_iso

    def test_z_suffix_parsed(self):
        fn = self._fn()
        result = fn("2025-01-15T12:00:00Z")
        assert result is not None
        assert result.year == 2025
        assert result.tzinfo is not None

    def test_plus_offset_parsed(self):
        fn = self._fn()
        result = fn("2025-01-15T12:00:00+00:00")
        assert result is not None
        assert result.hour == 12

    def test_invalid_string_returns_none(self):
        fn = self._fn()
        assert fn("not-a-date") is None

    def test_empty_string_returns_none(self):
        fn = self._fn()
        assert fn("") is None

    def test_strips_whitespace(self):
        fn = self._fn()
        result = fn("  2025-01-15T12:00:00Z  ")
        assert result is not None


# ---------------------------------------------------------------------------
# CacheReadResult
# ---------------------------------------------------------------------------


class TestCacheReadResult:
    def test_frozen_dataclass(self):
        from navig.cache_store import CacheReadResult

        r = CacheReadResult(hit=True, expired=False, data={"x": 1}, cached_at="2025-01-01T00:00:00Z")
        with pytest.raises((AttributeError, TypeError)):
            r.hit = False  # type: ignore[misc]

    def test_fields(self):
        from navig.cache_store import CacheReadResult

        r = CacheReadResult(hit=False, expired=True, data=None, cached_at=None)
        assert r.hit is False
        assert r.expired is True
        assert r.data is None
        assert r.cached_at is None


# ---------------------------------------------------------------------------
# read_json_cache
# ---------------------------------------------------------------------------


class TestReadJsonCache:
    def _read(self, filename, *, ttl_seconds, no_cache=False):
        from navig.cache_store import read_json_cache

        return read_json_cache(filename, ttl_seconds=ttl_seconds, no_cache=no_cache)

    def test_no_cache_flag_returns_miss(self, tmp_path):
        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            result = self._read("test.json", ttl_seconds=60, no_cache=True)
        assert result.hit is False
        assert result.data is None

    def test_missing_file_returns_miss(self, tmp_path):
        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            result = self._read("nonexistent.json", ttl_seconds=60)
        assert result.hit is False
        assert result.expired is False

    def test_fresh_cache_returns_data(self, tmp_path):
        from navig.cache_store import _to_iso_z

        now = datetime.now(timezone.utc)
        payload = {"cached_at": _to_iso_z(now), "data": {"key": "value"}}
        (tmp_path / "test.json").write_text(json.dumps(payload), encoding="utf-8")

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            with patch("navig.cache_store.utc_now", return_value=now):
                result = self._read("test.json", ttl_seconds=300)

        assert result.hit is True
        assert result.expired is False
        assert result.data == {"key": "value"}

    def test_expired_cache_returns_hit_expired(self, tmp_path):
        from navig.cache_store import _to_iso_z

        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        payload = {"cached_at": _to_iso_z(old_time), "data": {"stale": True}}
        (tmp_path / "old.json").write_text(json.dumps(payload), encoding="utf-8")

        now = datetime.now(timezone.utc)
        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            with patch("navig.cache_store.utc_now", return_value=now):
                result = self._read("old.json", ttl_seconds=60)

        assert result.hit is True
        assert result.expired is True
        assert result.data is None

    def test_negative_ttl_never_expires(self, tmp_path):
        """ttl_seconds=-1 means unlimited TTL."""
        from navig.cache_store import _to_iso_z

        very_old = datetime(2000, 1, 1, tzinfo=timezone.utc)
        payload = {"cached_at": _to_iso_z(very_old), "data": {"ancient": True}}
        (tmp_path / "forever.json").write_text(json.dumps(payload), encoding="utf-8")

        now = datetime.now(timezone.utc)
        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            with patch("navig.cache_store.utc_now", return_value=now):
                result = self._read("forever.json", ttl_seconds=-1)

        assert result.hit is True
        assert result.expired is False
        assert result.data == {"ancient": True}

    def test_corrupt_json_returns_miss(self, tmp_path):
        (tmp_path / "corrupt.json").write_text("!!!not json!!!", encoding="utf-8")

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            result = self._read("corrupt.json", ttl_seconds=60)

        assert result.hit is False

    def test_missing_cached_at_returns_miss(self, tmp_path):
        payload = {"data": {"x": 1}}  # no cached_at
        (tmp_path / "no_ts.json").write_text(json.dumps(payload), encoding="utf-8")

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            result = self._read("no_ts.json", ttl_seconds=60)

        assert result.hit is False


# ---------------------------------------------------------------------------
# write_json_cache
# ---------------------------------------------------------------------------


class TestWriteJsonCache:
    def test_writes_file(self, tmp_path):
        from navig.cache_store import write_json_cache

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            path = write_json_cache("out.json", {"hello": "world"})

        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["data"] == {"hello": "world"}
        assert "cached_at" in payload

    def test_creates_cache_dir(self, tmp_path):
        from navig.cache_store import write_json_cache

        subdir = tmp_path / "deep" / "cache"
        with patch("navig.cache_store.global_cache_dir", return_value=subdir):
            path = write_json_cache("f.json", [1, 2, 3])

        assert path.exists()

    def test_roundtrip_readable(self, tmp_path):
        from navig.cache_store import read_json_cache, write_json_cache

        now = datetime.now(timezone.utc)
        data = {"items": [1, 2, 3], "name": "test"}

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            with patch("navig.cache_store.utc_now", return_value=now):
                write_json_cache("rtrip.json", data)
                result = read_json_cache("rtrip.json", ttl_seconds=300)

        assert result.hit is True
        assert result.expired is False
        assert result.data == data

    def test_overwrites_existing_file(self, tmp_path):
        from navig.cache_store import write_json_cache

        with patch("navig.cache_store.global_cache_dir", return_value=tmp_path):
            write_json_cache("over.json", {"v": 1})
            write_json_cache("over.json", {"v": 2})
            path = tmp_path / "over.json"

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["data"]["v"] == 2
