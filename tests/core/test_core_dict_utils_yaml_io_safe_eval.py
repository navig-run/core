"""
Batch 78: navig/core/dict_utils.py, navig/core/yaml_io.py,
          navig/core/safe_eval.py
"""
from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# core/dict_utils.py
# ---------------------------------------------------------------------------
from navig.core.dict_utils import deep_merge, truncate_output, utc_now, now_iso


class TestDeepMerge:
    def test_override_leaf(self):
        result = deep_merge({"a": 1}, {"a": 2})
        assert result["a"] == 2

    def test_adds_new_key(self):
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_preserves_base_key_not_in_override(self):
        result = deep_merge({"a": 1, "b": 99}, {"a": 2})
        assert result["b"] == 99

    def test_recursive_dict_merge(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 9999}}
        result = deep_merge(base, override)
        assert result["db"]["host"] == "localhost"
        assert result["db"]["port"] == 9999

    def test_list_concatenation(self):
        base = {"items": [1, 2]}
        override = {"items": [3, 4]}
        result = deep_merge(base, override)
        assert result["items"] == [1, 2, 3, 4]

    def test_does_not_mutate_base(self):
        base = {"x": {"y": 1}}
        deep_merge(base, {"x": {"y": 2}})
        assert base["x"]["y"] == 1

    def test_empty_override(self):
        base = {"a": 1}
        result = deep_merge(base, {})
        assert result == {"a": 1}

    def test_empty_base(self):
        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_deeply_nested(self):
        base = {"l1": {"l2": {"l3": "base"}}}
        override = {"l1": {"l2": {"l3": "new", "l3b": "extra"}}}
        result = deep_merge(base, override)
        assert result["l1"]["l2"]["l3"] == "new"
        assert result["l1"]["l2"]["l3b"] == "extra"


class TestTruncateOutput:
    def test_short_text_unchanged(self):
        assert truncate_output("hello", 100) == "hello"

    def test_exactly_at_limit(self):
        text = "x" * 50
        assert truncate_output(text, 50) == text

    def test_truncates_long_text(self):
        text = "a" * 200
        result = truncate_output(text, 100)
        assert result.startswith("a" * 100)
        assert "truncated" in result
        assert "200" in result

    def test_empty_string(self):
        assert truncate_output("", 10) == ""


class TestUtcNow:
    def test_returns_aware_datetime(self):
        from datetime import timezone
        dt = utc_now()
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_now_iso_is_string(self):
        s = now_iso()
        assert isinstance(s, str)
        assert "+" in s or "Z" in s or "T" in s


# ---------------------------------------------------------------------------
# core/yaml_io.py
# ---------------------------------------------------------------------------
from navig.core.yaml_io import (
    safe_load_yaml,
    atomic_write_yaml,
    atomic_write_text,
    load_yaml_with_lines,
    YamlDocument,
)


class TestSafeLoadYaml:
    def test_missing_file_returns_none(self, tmp_path):
        result = safe_load_yaml(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnum: 42\n")
        result = safe_load_yaml(f)
        assert result == {"key": "value", "num": 42}

    def test_invalid_yaml_returns_none(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("key: [unclosed")
        result = safe_load_yaml(f)
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        result = safe_load_yaml(f)
        assert result is None


class TestAtomicWriteYaml:
    def test_roundtrip(self, tmp_path):
        f = tmp_path / "out.yaml"
        data = {"key": "value", "nested": {"a": 1}}
        atomic_write_yaml(data, f)
        result = safe_load_yaml(f)
        assert result["key"] == "value"
        assert result["nested"]["a"] == 1

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "deep" / "dir" / "out.yaml"
        atomic_write_yaml({"x": 1}, f)
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "out.yaml"
        atomic_write_yaml({"v": 1}, f)
        atomic_write_yaml({"v": 99}, f)
        result = safe_load_yaml(f)
        assert result["v"] == 99


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        f = tmp_path / "out.txt"
        atomic_write_text(f, "hello world")
        assert f.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.txt"
        atomic_write_text(f, "content")
        assert f.exists()

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "out.txt"
        atomic_write_text(f, "first")
        atomic_write_text(f, "second")
        assert f.read_text() == "second"


class TestLoadYamlWithLines:
    def test_returns_yaml_document(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnum: 42\n")
        doc = load_yaml_with_lines(f)
        assert isinstance(doc, YamlDocument)
        assert doc.data["key"] == "value"
        assert doc.data["num"] == "42"  # scalar values come back as strings via node API

    def test_line_map_populated(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nother: 2\n")
        doc = load_yaml_with_lines(f)
        assert len(doc.line_map) > 0

    def test_empty_yaml_gives_none_data(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        doc = load_yaml_with_lines(f)
        assert doc.data is None


# ---------------------------------------------------------------------------
# core/safe_eval.py
# ---------------------------------------------------------------------------
from navig.core.safe_eval import safe_eval


class TestSafeEval:
    # Arithmetic
    def test_addition(self):
        assert safe_eval("1 + 2") == 3

    def test_subtraction(self):
        assert safe_eval("10 - 4") == 6

    def test_multiplication(self):
        assert safe_eval("3 * 4") == 12

    def test_division(self):
        assert safe_eval("10 / 4") == 2.5

    def test_floor_division(self):
        assert safe_eval("10 // 3") == 3

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1

    def test_power(self):
        assert safe_eval("2 ** 10") == 1024

    # Comparison
    def test_equality(self):
        assert safe_eval("1 == 1") is True
        assert safe_eval("1 == 2") is False

    def test_less_than(self):
        assert safe_eval("3 < 5") is True

    def test_greater_equal(self):
        assert safe_eval("5 >= 5") is True

    # Variables
    def test_variable_substitution(self):
        assert safe_eval("x + y", {"x": 10, "y": 20}) == 30

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Unknown variable"):
            safe_eval("unknown_var")

    # Logic
    def test_boolean_and(self):
        assert safe_eval("True and False") is False

    def test_boolean_or(self):
        assert safe_eval("True or False") is True

    def test_not_operator(self):
        assert safe_eval("not True") is False

    # Data structures
    def test_list_literal(self):
        assert safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_dict_literal(self):
        assert safe_eval('{"a": 1}') == {"a": 1}

    def test_subscript(self):
        assert safe_eval("x[0]", {"x": [10, 20]}) == 10

    # Blocked features
    def test_function_call_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("len([1,2,3])")

    def test_invalid_expr_raises(self):
        with pytest.raises(ValueError):
            safe_eval(")(")
