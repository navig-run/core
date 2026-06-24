"""
Batch 95 — tests for navig.core.dict_utils, safe_eval, tokens, and yaml_io
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest


# =============================================================================
# dict_utils
# =============================================================================


class TestDeepMerge:
    def _merge(self, base, override):
        from navig.core.dict_utils import deep_merge
        return deep_merge(base, override)

    def test_simple_override(self):
        result = self._merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_dict_merged(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 3306, "user": "root"}}
        result = self._merge(base, override)
        assert result["db"]["host"] == "localhost"
        assert result["db"]["port"] == 3306
        assert result["db"]["user"] == "root"

    def test_list_concatenated(self):
        base = {"tags": ["a", "b"]}
        override = {"tags": ["c"]}
        result = self._merge(base, override)
        assert result["tags"] == ["a", "b", "c"]

    def test_scalar_override_supersedes(self):
        result = self._merge({"x": "old"}, {"x": "new"})
        assert result["x"] == "new"

    def test_does_not_mutate_base(self):
        base = {"nested": {"a": 1}}
        override = {"nested": {"b": 2}}
        orig_id = id(base["nested"])
        self._merge(base, override)
        assert base["nested"] == {"a": 1}

    def test_empty_override(self):
        base = {"a": 1}
        result = self._merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        result = self._merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_new_keys_from_override(self):
        result = self._merge({"a": 1}, {"b": 2, "c": 3})
        assert result["b"] == 2
        assert result["c"] == 3

    def test_deeply_nested(self):
        base = {"l1": {"l2": {"val": 0}}}
        override = {"l1": {"l2": {"val": 99, "extra": True}}}
        result = self._merge(base, override)
        assert result["l1"]["l2"]["val"] == 99
        assert result["l1"]["l2"]["extra"] is True


class TestTruncateOutput:
    def _truncate(self, text, limit):
        from navig.core.dict_utils import truncate_output
        return truncate_output(text, limit)

    def test_no_truncation_when_under_limit(self):
        assert self._truncate("hello", 10) == "hello"

    def test_no_truncation_when_exactly_limit(self):
        assert self._truncate("hello", 5) == "hello"

    def test_truncates_above_limit(self):
        result = self._truncate("hello world", 5)
        assert result.startswith("hello")
        assert "truncated" in result

    def test_includes_total_count(self):
        text = "a" * 100
        result = self._truncate(text, 10)
        assert "100" in result

    def test_empty_string(self):
        assert self._truncate("", 10) == ""


class TestUtcNow:
    def test_returns_datetime(self):
        from navig.core.dict_utils import utc_now
        result = utc_now()
        assert isinstance(result, datetime)

    def test_is_timezone_aware(self):
        from navig.core.dict_utils import utc_now
        result = utc_now()
        assert result.tzinfo is not None

    def test_is_utc(self):
        from navig.core.dict_utils import utc_now
        result = utc_now()
        assert result.utcoffset().total_seconds() == 0


class TestNowIso:
    def test_returns_string(self):
        from navig.core.dict_utils import now_iso
        result = now_iso()
        assert isinstance(result, str)

    def test_parseable_as_datetime(self):
        from navig.core.dict_utils import now_iso
        result = now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None


# =============================================================================
# safe_eval
# =============================================================================


class TestSafeEval:
    def _eval(self, expr, variables=None):
        from navig.core.safe_eval import safe_eval
        return safe_eval(expr, variables)

    # Arithmetic
    def test_addition(self):
        assert self._eval("1 + 2") == 3

    def test_subtraction(self):
        assert self._eval("10 - 3") == 7

    def test_multiplication(self):
        assert self._eval("4 * 5") == 20

    def test_division(self):
        assert self._eval("10 / 4") == 2.5

    def test_floor_division(self):
        assert self._eval("10 // 3") == 3

    def test_modulo(self):
        assert self._eval("10 % 3") == 1

    def test_power(self):
        assert self._eval("2 ** 8") == 256

    # Comparisons
    def test_eq_true(self):
        assert self._eval("1 == 1") is True

    def test_eq_false(self):
        assert self._eval("1 == 2") is False

    def test_lt(self):
        assert self._eval("1 < 2") is True

    def test_gt(self):
        assert self._eval("2 > 1") is True

    def test_ne(self):
        assert self._eval("1 != 2") is True

    def test_ge(self):
        assert self._eval("2 >= 2") is True

    def test_le(self):
        assert self._eval("1 <= 1") is True

    # Boolean
    def test_and_true(self):
        assert self._eval("True and True") is True

    def test_and_false(self):
        assert self._eval("True and False") is False

    def test_or_true(self):
        assert self._eval("False or True") is True

    def test_or_false(self):
        assert self._eval("False or False") is False

    def test_not(self):
        assert self._eval("not True") is False

    # Variables
    def test_variable_access(self):
        assert self._eval("x + 1", {"x": 10}) == 11

    def test_multiple_variables(self):
        assert self._eval("a * b", {"a": 3, "b": 4}) == 12

    def test_variable_in_comparison(self):
        assert self._eval("x > 5", {"x": 10}) is True

    def test_variable_false(self):
        assert self._eval("x > 5", {"x": 3}) is False

    # Literals
    def test_string_literal(self):
        assert self._eval("'hello'") == "hello"

    def test_list_literal(self):
        assert self._eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self):
        assert self._eval("(1, 2)") == (1, 2)

    def test_dict_literal(self):
        assert self._eval("{'a': 1}") == {"a": 1}

    # Subscript
    def test_list_subscript(self):
        assert self._eval("items[0]", {"items": [10, 20]}) == 10

    def test_dict_subscript(self):
        assert self._eval("d['key']", {"d": {"key": "val"}}) == "val"

    # in / not in
    def test_in_operator(self):
        assert self._eval("2 in items", {"items": [1, 2, 3]}) is True

    def test_not_in_operator(self):
        assert self._eval("5 not in items", {"items": [1, 2, 3]}) is True

    # Errors
    def test_unknown_variable_raises(self):
        from navig.core.safe_eval import safe_eval
        with pytest.raises(ValueError, match="Unknown variable"):
            safe_eval("unknown_var")

    def test_function_call_blocked(self):
        from navig.core.safe_eval import safe_eval
        with pytest.raises(ValueError):
            safe_eval("abs(-1)")

    def test_attribute_access_blocked(self):
        from navig.core.safe_eval import safe_eval
        with pytest.raises(ValueError):
            safe_eval("x.upper()", {"x": "hello"})

    def test_invalid_syntax_raises(self):
        from navig.core.safe_eval import safe_eval
        with pytest.raises(ValueError):
            safe_eval("1 +* 2")


# =============================================================================
# tokens
# =============================================================================


class TestEstimateTokens:
    def _est(self, text, **kw):
        from navig.core.tokens import estimate_tokens
        return estimate_tokens(text, **kw)

    def test_empty_string_returns_zero(self):
        assert self._est("") == 0

    def test_non_empty_returns_at_least_one(self):
        assert self._est("x") >= 1

    def test_default_ratio(self):
        assert self._est("a" * 40) == 10  # 40 / 4.0 = 10

    def test_custom_ratio(self):
        assert self._est("a" * 35, chars_per_token=3.5) == 10

    def test_long_text(self):
        text = "word " * 1000  # 5000 chars
        result = self._est(text)
        assert result == 1250  # 5000 / 4.0

    def test_returns_int(self):
        result = self._est("hello world")
        assert isinstance(result, int)

    def test_minimum_one_for_single_char(self):
        assert self._est("x") == 1


# =============================================================================
# yaml_io
# =============================================================================


class TestSafeLoadYaml:
    def test_returns_none_for_missing_file(self, tmp_path):
        from navig.core.yaml_io import safe_load_yaml
        assert safe_load_yaml(tmp_path / "nonexistent.yaml") is None

    def test_returns_none_for_empty_file(self, tmp_path):
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert safe_load_yaml(f) is None

    def test_loads_valid_yaml(self, tmp_path):
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "config.yaml"
        f.write_text("name: test\nvalue: 42\n")
        result = safe_load_yaml(f)
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "bad.yaml"
        f.write_text("key: {unclosed")
        result = safe_load_yaml(f)
        assert result is None

    def test_accepts_string_path(self, tmp_path):
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "test.yaml"
        f.write_text("x: 1\n")
        result = safe_load_yaml(str(f))
        assert result["x"] == 1


class TestAtomicWriteYaml:
    def test_writes_yaml_file(self, tmp_path):
        from navig.core.yaml_io import atomic_write_yaml
        f = tmp_path / "out.yaml"
        atomic_write_yaml({"key": "value", "num": 42}, f)
        import yaml
        data = yaml.safe_load(f.read_text())
        assert data["key"] == "value"
        assert data["num"] == 42

    def test_creates_parent_dirs(self, tmp_path):
        from navig.core.yaml_io import atomic_write_yaml
        f = tmp_path / "sub" / "dir" / "out.yaml"
        atomic_write_yaml({"a": 1}, f)
        assert f.exists()

    def test_overwrites_existing_file(self, tmp_path):
        from navig.core.yaml_io import atomic_write_yaml
        f = tmp_path / "out.yaml"
        f.write_text("old: data\n")
        atomic_write_yaml({"new": "content"}, f)
        import yaml
        data = yaml.safe_load(f.read_text())
        assert data.get("new") == "content"
        assert "old" not in data

    def test_accepts_string_path(self, tmp_path):
        from navig.core.yaml_io import atomic_write_yaml
        f = tmp_path / "str.yaml"
        atomic_write_yaml({"x": 1}, str(f))
        assert f.exists()


class TestAtomicWriteText:
    def test_writes_text_file(self, tmp_path):
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "out.txt"
        atomic_write_text(f, "hello world")
        assert f.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "a" / "b" / "out.txt"
        atomic_write_text(f, "content")
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "out.txt"
        f.write_text("old")
        atomic_write_text(f, "new")
        assert f.read_text() == "new"

    def test_custom_encoding(self, tmp_path):
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "unicode.txt"
        atomic_write_text(f, "héllo wörld", encoding="utf-8")
        assert "héllo" in f.read_text(encoding="utf-8")

    def test_accepts_string_path(self, tmp_path):
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "s.txt"
        atomic_write_text(str(f), "data")
        assert f.read_text() == "data"


class TestLogShadowAnomaly:
    def test_does_not_raise(self, tmp_path):
        from navig.core import yaml_io
        orig = yaml_io._PERF_DIR
        yaml_io._PERF_DIR = tmp_path / "perf"
        try:
            yaml_io.log_shadow_anomaly("test-log", "test_event", {"key": "value"})
        finally:
            yaml_io._PERF_DIR = orig

    def test_creates_jsonl_file(self, tmp_path):
        from navig.core import yaml_io
        import json
        perf_dir = tmp_path / "perf"
        orig = yaml_io._PERF_DIR
        yaml_io._PERF_DIR = perf_dir
        try:
            yaml_io.log_shadow_anomaly("mylog", "evt", {"x": 1})
            log_file = perf_dir / "mylog.jsonl"
            assert log_file.exists()
            rows = [json.loads(l) for l in log_file.read_text().splitlines()]
            assert rows[0]["event"] == "evt"
            assert rows[0]["data"] == {"x": 1}
        finally:
            yaml_io._PERF_DIR = orig

    def test_appends_multiple_entries(self, tmp_path):
        from navig.core import yaml_io
        import json
        perf_dir = tmp_path / "perf"
        orig = yaml_io._PERF_DIR
        yaml_io._PERF_DIR = perf_dir
        try:
            yaml_io.log_shadow_anomaly("log2", "e1", {})
            yaml_io.log_shadow_anomaly("log2", "e2", {})
            log_file = perf_dir / "log2.jsonl"
            rows = [json.loads(l) for l in log_file.read_text().splitlines()]
            assert len(rows) == 2
            assert rows[1]["event"] == "e2"
        finally:
            yaml_io._PERF_DIR = orig
