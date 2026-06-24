"""Tests for navig.tools.domains.system_pack — _system_info, _file_read."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from navig.tools.domains.system_pack import _file_read, _system_info


class TestSystemInfo:
    def test_returns_dict(self):
        result = _system_info()
        assert isinstance(result, dict)

    def test_contains_expected_keys(self):
        result = _system_info()
        for key in ("platform", "python", "machine", "node"):
            assert key in result

    def test_python_version_matches(self):
        result = _system_info()
        import platform
        assert result["python"] == platform.python_version()

    def test_kwargs_ignored(self):
        result = _system_info(unused="param")
        assert "platform" in result


class TestFileRead:
    def test_error_on_missing_file(self, tmp_path):
        result = _file_read(str(tmp_path / "nonexistent.txt"))
        assert "error" in result

    def test_reads_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        result = _file_read(str(f))
        assert result["lines"] == ["line1", "line2", "line3"]

    def test_not_truncated_when_under_limit(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("only one line\n")
        result = _file_read(str(f))
        assert result["truncated"] is False

    def test_truncated_when_over_limit(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(str(i) for i in range(300)))
        result = _file_read(str(f), max_lines=50)
        assert result["truncated"] is True
        assert len(result["lines"]) == 50

    def test_total_lines_reflects_full_count(self, tmp_path):
        f = tmp_path / "counts.txt"
        f.write_text("\n".join(str(i) for i in range(10)))
        result = _file_read(str(f))
        assert result["total_lines"] == 10

    def test_path_field_in_result(self, tmp_path):
        f = tmp_path / "path_test.txt"
        f.write_text("hello")
        result = _file_read(str(f))
        assert result["path"] == str(f.resolve())

    def test_tilde_expansion(self):
        # ~/nonexistent should error gracefully, not crash
        result = _file_read("~/no_such_file_navig_test.txt")
        assert "error" in result

    def test_kwargs_ignored(self, tmp_path):
        f = tmp_path / "kw.txt"
        f.write_text("x")
        result = _file_read(str(f), extra_kwarg="ignored")
        assert "lines" in result
