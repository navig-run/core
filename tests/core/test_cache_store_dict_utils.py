"""
Batch 83 — navig/cache_store.py + navig/core/dict_utils.py
"""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from navig.cache_store import (
    CacheReadResult,
    _parse_iso,
    _to_iso_z,
    read_json_cache,
    write_json_cache,
)
from navig.core.dict_utils import deep_merge, now_iso, truncate_output, utc_now


# ---------------------------------------------------------------------------
# dict_utils helpers
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_simple_override(self):
        result = deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_base_keys_preserved(self):
        result = deep_merge({"x": 10}, {"y": 20})
        assert result["x"] == 10
        assert result["y"] == 20

    def test_nested_dict_merged(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 3306}}
        result = deep_merge(base, override)
        assert result["db"]["host"] == "localhost"
        assert result["db"]["port"] == 3306

    def test_list_values_concatenated(self):
        base = {"tags": ["a", "b"]}
        override = {"tags": ["c"]}
        result = deep_merge(base, override)
        assert result["tags"] == ["a", "b", "c"]

    def test_deep_copy_leaf(self):
        mutable = [1, 2, 3]
        result = deep_merge({}, {"items": mutable})
        mutable.append(99)
        # deep copy means result is unaffected
        assert result["items"] == [1, 2, 3]

    def test_empty_base(self):
        result = deep_merge({}, {"k": "v"})
        assert result == {"k": "v"}

    def test_empty_override(self):
        result = deep_merge({"k": "v"}, {})
        assert result == {"k": "v"}

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1}}}
        override = {"a": {"b": {"d": 2}}}
        result = deep_merge(base, override)
        assert result["a"]["b"]["c"] == 1
        assert result["a"]["b"]["d"] == 2


class TestTruncateOutput:
    def test_short_text_unchanged(self):
        assert truncate_output("hello", 100) == "hello"

    def test_exact_limit_unchanged(self):
        text = "a" * 50
        assert truncate_output(text, 50) == text

    def test_long_text_truncated(self):
        result = truncate_output("x" * 200, 100)
        assert result.startswith("x" * 100)
        assert "truncated" in result.lower() or "200" in result

    def test_empty_string(self):
        assert truncate_output("", 10) == ""


class TestUtcNow:
    def test_returns_datetime(self):
        result = utc_now()
        assert isinstance(result, datetime)

    def test_timezone_aware(self):
        result = utc_now()
        assert result.tzinfo is not None

    def test_utc_zone(self):
        result = utc_now()
        assert result.utcoffset().total_seconds() == 0


class TestNowIso:
    def test_returns_string(self):
        assert isinstance(now_iso(), str)

    def test_contains_plus_offset(self):
        # ISO format with timezone offset
        s = now_iso()
        assert "+" in s or "Z" in s or s.endswith("+00:00")

    def test_parseable(self):
        s = now_iso()
        dt = datetime.fromisoformat(s)
        assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# cache_store helpers
# ---------------------------------------------------------------------------


class TestToIsoZ:
    def test_utc_datetime_ends_with_z(self):
        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _to_iso_z(dt)
        assert result.endswith("Z")

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2025, 1, 15, 10, 30, 0)
        result = _to_iso_z(dt)
        assert result.endswith("Z")


class TestParseIso:
    def test_z_suffix(self):
        dt = _parse_iso("2025-01-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_offset_suffix(self):
        dt = _parse_iso("2025-01-15T10:30:00+00:00")
        assert dt is not None

    def test_invalid_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_empty_returns_none(self):
        assert _parse_iso("") is None


# ---------------------------------------------------------------------------
# CacheReadResult dataclass
# ---------------------------------------------------------------------------


class TestCacheReadResult:
    def test_hit_false(self):
        r = CacheReadResult(hit=False, expired=False, data=None, cached_at=None)
        assert r.hit is False

    def test_data_accessible(self):
        r = CacheReadResult(hit=True, expired=False, data={"k": "v"}, cached_at="2025-01-01T00:00:00Z")
        assert r.data == {"k": "v"}


# ---------------------------------------------------------------------------
# write_json_cache / read_json_cache
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_cache_dir(tmp_path, monkeypatch):
    """Redirect global_cache_dir() to tmp_path."""
    monkeypatch.setattr("navig.cache_store.global_cache_dir", lambda: tmp_path)
    return tmp_path


class TestWriteJsonCache:
    def test_creates_file(self, fake_cache_dir, tmp_path):
        write_json_cache("test.json", {"key": "value"})
        assert (tmp_path / "test.json").exists()

    def test_file_has_cached_at(self, fake_cache_dir, tmp_path):
        write_json_cache("x.json", [1, 2, 3])
        payload = json.loads((tmp_path / "x.json").read_text())
        assert "cached_at" in payload
        assert payload["cached_at"].endswith("Z")

    def test_file_data_matches(self, fake_cache_dir, tmp_path):
        write_json_cache("data.json", {"answer": 42})
        payload = json.loads((tmp_path / "data.json").read_text())
        assert payload["data"] == {"answer": 42}

    def test_returns_path(self, fake_cache_dir, tmp_path):
        result = write_json_cache("r.json", "hello")
        assert result == tmp_path / "r.json"


class TestReadJsonCache:
    def test_miss_when_file_absent(self, fake_cache_dir):
        result = read_json_cache("absent.json", ttl_seconds=60)
        assert result.hit is False
        assert result.data is None

    def test_no_cache_flag(self, fake_cache_dir, tmp_path):
        write_json_cache("existing.json", {"val": 1})
        result = read_json_cache("existing.json", ttl_seconds=600, no_cache=True)
        assert result.hit is False

    def test_fresh_hit(self, fake_cache_dir, tmp_path):
        write_json_cache("fresh.json", {"val": 99})
        result = read_json_cache("fresh.json", ttl_seconds=600)
        assert result.hit is True
        assert result.expired is False
        assert result.data == {"val": 99}

    def test_expired_hit(self, fake_cache_dir, tmp_path):
        # Write cache with a past timestamp by patching utc_now
        past = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        with patch("navig.cache_store.utc_now", return_value=past):
            write_json_cache("old.json", {"old": True})
        # Now read with ttl=10 (file is ~25 years old)
        result = read_json_cache("old.json", ttl_seconds=10)
        assert result.hit is True
        assert result.expired is True
        assert result.data is None

    def test_negative_ttl_never_expires(self, fake_cache_dir, tmp_path):
        past = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        with patch("navig.cache_store.utc_now", return_value=past):
            write_json_cache("neg.json", {"v": 7})
        result = read_json_cache("neg.json", ttl_seconds=-1)
        assert result.hit is True
        assert result.expired is False
        assert result.data == {"v": 7}

    def test_malformed_file_returns_miss(self, fake_cache_dir, tmp_path):
        (tmp_path / "bad.json").write_text("not json!!!")
        result = read_json_cache("bad.json", ttl_seconds=60)
        assert result.hit is False

    def test_missing_cached_at_returns_miss(self, fake_cache_dir, tmp_path):
        (tmp_path / "no_ts.json").write_text(json.dumps({"data": {"x": 1}}))
        result = read_json_cache("no_ts.json", ttl_seconds=60)
        assert result.hit is False
