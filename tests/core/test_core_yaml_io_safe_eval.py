"""
Batch 75: hermetic unit tests for
  - navig/core/yaml_io.py    (atomic_write_yaml, atomic_write_text, safe_load_yaml,
                               load_yaml_with_lines, YamlDocument)
  - navig/core/safe_eval.py  (safe_eval)
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# navig/core/yaml_io.py
# ---------------------------------------------------------------------------

class TestAtomicWriteText:
    def test_writes_content(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "out.txt"
        atomic_write_text(f, "hello world")
        assert f.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "a" / "b" / "c.txt"
        atomic_write_text(f, "nested")
        assert f.read_text(encoding="utf-8") == "nested"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "out.txt"
        f.write_text("old")
        atomic_write_text(f, "new")
        assert f.read_text(encoding="utf-8") == "new"

    def test_empty_string(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_text
        f = tmp_path / "empty.txt"
        atomic_write_text(f, "")
        assert f.read_text(encoding="utf-8") == ""


class TestAtomicWriteYaml:
    def test_writes_dict(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_yaml, safe_load_yaml
        f = tmp_path / "data.yaml"
        atomic_write_yaml({"key": "value", "num": 42}, f)
        loaded = safe_load_yaml(f)
        assert loaded == {"key": "value", "num": 42}

    def test_writes_list(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_yaml, safe_load_yaml
        f = tmp_path / "list.yaml"
        atomic_write_yaml([1, 2, 3], f)
        assert safe_load_yaml(f) == [1, 2, 3]

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import atomic_write_yaml
        f = tmp_path / "deep" / "nested" / "out.yaml"
        atomic_write_yaml({"x": 1}, f)
        assert f.exists()


class TestSafeLoadYaml:
    def test_returns_none_for_nonexistent_file(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import safe_load_yaml
        result = safe_load_yaml(tmp_path / "missing.yaml")
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "empty.yaml"
        f.write_text("")
        assert safe_load_yaml(f) is None

    def test_returns_none_for_invalid_yaml(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "bad.yaml"
        f.write_text("key: {unclosed")
        assert safe_load_yaml(f) is None

    def test_returns_parsed_dict(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import safe_load_yaml
        f = tmp_path / "good.yaml"
        f.write_text("host: example.com\nport: 22\n")
        result = safe_load_yaml(f)
        assert result == {"host": "example.com", "port": 22}


class TestLoadYamlWithLines:
    def test_data_not_none_for_valid_yaml(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import load_yaml_with_lines
        f = tmp_path / "test.yaml"
        f.write_text("a: hello\nb: world\n")
        doc = load_yaml_with_lines(f)
        assert doc.data is not None

    def test_line_map_not_empty(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import load_yaml_with_lines
        f = tmp_path / "test.yaml"
        f.write_text("first: value\nsecond: other\n")
        doc = load_yaml_with_lines(f)
        assert len(doc.line_map) > 0

    def test_empty_file_returns_none_data(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import load_yaml_with_lines
        f = tmp_path / "empty.yaml"
        f.write_text("")
        doc = load_yaml_with_lines(f)
        assert doc.data is None

    def test_keys_map_to_positive_line_numbers(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import load_yaml_with_lines
        f = tmp_path / "lines.yaml"
        f.write_text("alpha: 1\nbeta: 2\ngamma: 3\n")
        doc = load_yaml_with_lines(f)
        assert all(isinstance(v, int) and v >= 1 for v in doc.line_map.values())

    def test_nested_keys_tracked(self, tmp_path: Path) -> None:
        from navig.core.yaml_io import load_yaml_with_lines
        f = tmp_path / "nested.yaml"
        f.write_text("top:\n  inner: value\n")
        doc = load_yaml_with_lines(f)
        assert len(doc.line_map) >= 1


# ---------------------------------------------------------------------------
# navig/core/safe_eval.py
# ---------------------------------------------------------------------------

class TestSafeEval:
    def test_arithmetic_add(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("2 + 3") == 5

    def test_arithmetic_multiply(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("4 * 7") == 28

    def test_arithmetic_division(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("10 / 4") == 2.5

    def test_floor_division(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("10 // 3") == 3

    def test_modulo(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("10 % 3") == 1

    def test_power(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("2 ** 8") == 256

    def test_comparison_equal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("5 == 5") is True

    def test_comparison_not_equal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("4 != 5") is True

    def test_comparison_less_than(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("3 < 4") is True

    def test_logical_and(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("True and False") is False

    def test_logical_or(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("True or False") is True

    def test_unary_negation(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("-5") == -5

    def test_variables(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("x + y", {"x": 10, "y": 20}) == 30

    def test_string_literal(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval('"hello"') == "hello"

    def test_in_operator(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("2 in [1, 2, 3]") is True

    def test_not_in_operator(self) -> None:
        from navig.core.safe_eval import safe_eval
        assert safe_eval("5 not in [1, 2, 3]") is True

    def test_raises_on_invalid_expr(self) -> None:
        from navig.core.safe_eval import safe_eval
        with pytest.raises((ValueError, SyntaxError)):
            safe_eval("import os")

    def test_blocks_attribute_access(self) -> None:
        from navig.core.safe_eval import safe_eval
        with pytest.raises((ValueError, AttributeError, TypeError)):
            safe_eval("x.something", {"x": {}})

    def test_blocks_function_calls(self) -> None:
        from navig.core.safe_eval import safe_eval
        with pytest.raises((ValueError, TypeError, NameError)):
            safe_eval("open('file')")
