"""
Tests for navig.agent.tools.file_tools
"""

import asyncio
import os
from pathlib import Path

import pytest

from navig.agent.tools.file_tools import (
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
    _MAX_READ_CHARS,
    _MAX_WRITE_CHARS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


class TestReadFileTool:
    def setup_method(self):
        self.tool = ReadFileTool()

    def test_name(self):
        assert self.tool.name == "read_file"

    def test_missing_path_returns_failure(self):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "path" in result.error

    def test_nonexistent_file_returns_failure(self, tmp_path):
        result = _run(self.tool.run({"path": str(tmp_path / "missing.txt")}))
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_directory_returns_failure(self, tmp_path):
        result = _run(self.tool.run({"path": str(tmp_path)}))
        assert result.success is False
        assert "not a file" in result.error.lower()

    def test_reads_file_content(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = _run(self.tool.run({"path": str(f)}))
        assert result.success is True
        assert "hello world" in result.output

    def test_truncates_large_file(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * (_MAX_READ_CHARS + 1000), encoding="utf-8")
        result = _run(self.tool.run({"path": str(f)}))
        assert result.success is True
        assert "truncated" in result.output

    def test_start_end_line_range(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        result = _run(self.tool.run({"path": str(f), "start_line": 2, "end_line": 3}))
        assert result.success is True
        assert "line2" in result.output
        assert "line3" in result.output
        assert "line1" not in result.output
        assert "line4" not in result.output

    def test_elapsed_ms_present(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("x")
        result = _run(self.tool.run({"path": str(f)}))
        assert result.elapsed_ms >= 0


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


class TestWriteFileTool:
    def setup_method(self):
        self.tool = WriteFileTool()

    def test_name(self):
        assert self.tool.name == "write_file"

    def test_owner_only_true(self):
        assert self.tool.owner_only is True

    def test_missing_path_returns_failure(self):
        result = _run(self.tool.run({"content": "hello"}))
        assert result.success is False
        assert "path" in result.error

    def test_non_string_content_returns_failure(self, tmp_path):
        result = _run(self.tool.run({"path": str(tmp_path / "f.txt"), "content": 42}))
        assert result.success is False
        assert "string" in result.error

    def test_content_too_large_returns_failure(self, tmp_path):
        result = _run(
            self.tool.run(
                {"path": str(tmp_path / "f.txt"), "content": "x" * (_MAX_WRITE_CHARS + 1)}
            )
        )
        assert result.success is False
        assert "large" in result.error.lower() or "max" in result.error.lower()

    def test_writes_file(self, tmp_path):
        f = tmp_path / "sub" / "new.txt"
        result = _run(self.tool.run({"path": str(f), "content": "hello"}))
        assert result.success is True
        assert f.read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing_file(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content")
        result = _run(self.tool.run({"path": str(f), "content": "new content"}))
        assert result.success is True
        assert f.read_text(encoding="utf-8") == "new content"

    def test_append_mode(self, tmp_path):
        f = tmp_path / "append.txt"
        f.write_text("line1\n")
        result = _run(self.tool.run({"path": str(f), "content": "line2\n", "append": True}))
        assert result.success is True
        assert f.read_text(encoding="utf-8") == "line1\nline2\n"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "deep" / "nested" / "out.txt"
        result = _run(self.tool.run({"path": str(f), "content": "data"}))
        assert result.success is True
        assert f.exists()

    def test_output_mentions_char_count(self, tmp_path):
        f = tmp_path / "out.txt"
        result = _run(self.tool.run({"path": str(f), "content": "abc"}))
        assert result.success is True
        assert "3" in result.output


# ---------------------------------------------------------------------------
# ListFilesTool
# ---------------------------------------------------------------------------


class TestListFilesTool:
    def setup_method(self):
        self.tool = ListFilesTool()

    def test_name(self):
        assert self.tool.name == "list_files"

    def test_nonexistent_path_returns_failure(self, tmp_path):
        result = _run(self.tool.run({"path": str(tmp_path / "ghost")}))
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_lists_files_in_dir(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        result = _run(self.tool.run({"path": str(tmp_path)}))
        assert result.success is True
        names = [e["name"] for e in result.output["entries"]]
        assert any("a.txt" in n for n in names)
        assert any("b.py" in n for n in names)

    def test_hidden_files_excluded_by_default(self, tmp_path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("hi")
        result = _run(self.tool.run({"path": str(tmp_path)}))
        assert result.success is True
        names = [e["name"] for e in result.output["entries"]]
        assert not any(".hidden" in n for n in names)
        assert any("visible.txt" in n for n in names)

    def test_show_hidden_includes_dotfiles(self, tmp_path):
        (tmp_path / ".hidden").write_text("secret")
        result = _run(self.tool.run({"path": str(tmp_path), "show_hidden": True}))
        assert result.success is True
        names = [e["name"] for e in result.output["entries"]]
        assert any(".hidden" in n for n in names)

    def test_pattern_filter(self, tmp_path):
        (tmp_path / "script.py").write_text("x")
        (tmp_path / "readme.md").write_text("y")
        result = _run(self.tool.run({"path": str(tmp_path), "pattern": "*.py"}))
        assert result.success is True
        names = [e["name"] for e in result.output["entries"]]
        assert any(".py" in n for n in names)
        assert all(".md" not in n for n in names)

    def test_recursive_finds_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("deep")
        result = _run(self.tool.run({"path": str(tmp_path), "recursive": True}))
        assert result.success is True
        names = [e["name"] for e in result.output["entries"]]
        assert any("nested.txt" in n for n in names)

    def test_returns_entry_types(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "subdir").mkdir()
        result = _run(self.tool.run({"path": str(tmp_path)}))
        types = {e["name"]: e["type"] for e in result.output["entries"]}
        assert types.get("file.txt") == "file"
        assert types.get("subdir") == "dir"

    def test_file_entries_have_size(self, tmp_path):
        (tmp_path / "sized.txt").write_text("hello")
        result = _run(self.tool.run({"path": str(tmp_path)}))
        assert result.success is True
        file_entries = [e for e in result.output["entries"] if e["type"] == "file"]
        assert all(e["size"] is not None for e in file_entries)

    def test_count_in_output(self, tmp_path):
        for i in range(3):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        result = _run(self.tool.run({"path": str(tmp_path)}))
        assert result.output["count"] == 3
