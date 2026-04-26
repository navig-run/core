"""Tests for navig.core.dict_utils — deep_merge, truncate_output, utc helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from navig.core.dict_utils import deep_merge, now_iso, truncate_output, utc_now


# ──────────────────────────────────────────────────────────────
# deep_merge
# ──────────────────────────────────────────────────────────────


class TestDeepMerge:
    def test_simple_override(self):
        result = deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_base_keys_not_in_override_preserved(self):
        result = deep_merge({"keep": "yes", "also": "yes"}, {"also": "overridden"})
        assert result["keep"] == "yes"

    def test_nested_dict_merged_recursively(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 9999, "name": "mydb"}}
        result = deep_merge(base, override)
        assert result["db"]["host"] == "localhost"
        assert result["db"]["port"] == 9999
        assert result["db"]["name"] == "mydb"

    def test_lists_concatenated(self):
        base = {"tags": ["a", "b"]}
        override = {"tags": ["c", "d"]}
        result = deep_merge(base, override)
        assert result["tags"] == ["a", "b", "c", "d"]

    def test_empty_override_returns_copy_of_base(self):
        base = {"x": 1, "y": [1, 2]}
        result = deep_merge(base, {})
        assert result == base
        assert result is not base

    def test_empty_base_returns_copy_of_override(self):
        override = {"x": 42}
        result = deep_merge({}, override)
        assert result == override

    def test_both_empty(self):
        assert deep_merge({}, {}) == {}

    def test_does_not_mutate_base(self):
        base = {"a": {"nested": 1}}
        deep_merge(base, {"a": {"nested": 2}})
        assert base["a"]["nested"] == 1

    def test_does_not_mutate_override(self):
        override = {"list": [1, 2]}
        deep_merge({"list": [0]}, override)
        assert override["list"] == [1, 2]

    def test_scalar_override_beats_scalar(self):
        result = deep_merge({"v": "old"}, {"v": "new"})
        assert result["v"] == "new"

    def test_override_value_deep_copied(self):
        inner = {"key": "value"}
        result = deep_merge({}, {"obj": inner})
        result["obj"]["key"] = "mutated"
        assert inner["key"] == "value"  # original untouched

    def test_deeply_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = deep_merge(base, override)
        assert result["a"]["b"]["c"] == 99
        assert result["a"]["b"]["d"] == 2


# ──────────────────────────────────────────────────────────────
# truncate_output
# ──────────────────────────────────────────────────────────────


class TestTruncateOutput:
    def test_short_text_unchanged(self):
        assert truncate_output("hello", 100) == "hello"

    def test_exact_limit_unchanged(self):
        text = "x" * 50
        assert truncate_output(text, 50) == text

    def test_long_text_truncated(self):
        text = "a" * 200
        result = truncate_output(text, 100)
        assert len(result) > 100  # includes the note
        assert "truncated" in result
        assert result.startswith("a" * 100)

    def test_truncation_includes_total_char_count(self):
        text = "b" * 500
        result = truncate_output(text, 10)
        assert "500" in result

    def test_empty_string_unchanged(self):
        assert truncate_output("", 10) == ""

    def test_zero_limit(self):
        result = truncate_output("hello", 0)
        assert "truncated" in result


# ──────────────────────────────────────────────────────────────
# utc_now / now_iso
# ──────────────────────────────────────────────────────────────


class TestUtcHelpers:
    def test_utc_now_returns_datetime(self):
        result = utc_now()
        assert isinstance(result, datetime)

    def test_utc_now_is_timezone_aware(self):
        result = utc_now()
        assert result.tzinfo is not None

    def test_utc_now_is_utc(self):
        result = utc_now()
        assert result.utcoffset().total_seconds() == 0

    def test_now_iso_returns_string(self):
        result = now_iso()
        assert isinstance(result, str)

    def test_now_iso_contains_t_separator(self):
        result = now_iso()
        assert "T" in result

    def test_now_iso_contains_timezone_info(self):
        result = now_iso()
        assert "+" in result or "Z" in result or "-" in result[10:]
