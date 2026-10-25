"""
Tests for navig.agent.tools.file_tools
"""

import asyncio
import hashlib
from pathlib import Path

from navig.agent.tools.file_tools import (
    _MAX_READ_CHARS,
    _MAX_WRITE_CHARS,
    EditFileTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
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

    def test_close_time_write_failure_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        """A disk-full / quota error surfaces at flush/close, not at write().

        Text-mode writes buffer, so the OS write happens when the handle closes.
        The tool must surface that as success=False — it must NOT return
        "Written N chars" over a file whose bytes never reached disk. Before the
        fix (an un-``with``'d ``open().write()``) the close ran at GC finalization,
        which swallows the error, and the tool reported success.
        """
        target = tmp_path / "phantom.txt"

        class _CloseFails:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                raise OSError(28, "No space left on device")

            def write(self, data):  # buffered — succeeds, like the real handle
                return len(data)

            def close(self):
                raise OSError(28, "No space left on device")

        real_open = Path.open

        def fake_open(self, *args, **kwargs):
            if self.name == "phantom.txt":
                return _CloseFails()
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)

        result = _run(self.tool.run({"path": str(target), "content": "x" * 1000}))
        assert result.success is False
        assert "space" in (result.error or "").lower()


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


# ---------------------------------------------------------------------------
# EditFileTool
# ---------------------------------------------------------------------------


class TestEditFileTool:
    def setup_method(self):
        self.tool = EditFileTool()

    def test_name(self):
        assert self.tool.name == "edit_file"

    def test_owner_only_true(self):
        assert self.tool.owner_only is True

    def test_unique_replace_succeeds_with_diff(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("value = 1\n", encoding="utf-8")
        result = _run(
            self.tool.run({"path": str(f), "old_string": "value = 1", "new_string": "value = 2"})
        )
        assert result.success is True
        assert "Replaced 1 occurrence(s)" in result.output
        assert f.read_text(encoding="utf-8") == "value = 2\n"
        # bounded diff present
        assert "value = 2" in result.output

    def test_zero_match_refused_with_guidance(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("nothing here\n", encoding="utf-8")
        result = _run(
            self.tool.run({"path": str(f), "old_string": "missing text", "new_string": "x"})
        )
        assert result.success is False
        assert "not found" in result.error
        assert "missing text" in result.error

    def test_ambiguous_match_refused_unless_replace_all(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("dup\ndup\n", encoding="utf-8")
        refused = _run(self.tool.run({"path": str(f), "old_string": "dup", "new_string": "x"}))
        assert refused.success is False
        assert "matches 2 times" in refused.error
        assert "replace_all" in refused.error
        assert f.read_text(encoding="utf-8") == "dup\ndup\n"  # untouched

        all_replace = _run(
            self.tool.run({"path": str(f), "old_string": "dup", "new_string": "x", "replace_all": True})
        )
        assert all_replace.success is True
        assert f.read_text(encoding="utf-8") == "x\nx\n"

    def test_expected_hash_fresh_succeeds(self, tmp_path):
        f = tmp_path / "code.py"
        data = b"alpha\n"
        f.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        result = _run(
            self.tool.run(
                {"path": str(f), "old_string": "alpha", "new_string": "beta", "expected_hash": digest}
            )
        )
        assert result.success is True

    def test_expected_hash_stale_refused(self, tmp_path):
        """A concurrent writer between read and edit must never be silently
        overwritten — the stale hash refuses the edit."""
        f = tmp_path / "code.py"
        f.write_bytes(b"original\n")
        stale = hashlib.sha256(b"original\n").hexdigest()
        f.write_bytes(b"someone else wrote this\n")  # concurrent change
        result = _run(
            self.tool.run(
                {"path": str(f), "old_string": "original", "new_string": "mine", "expected_hash": stale}
            )
        )
        assert result.success is False
        assert "changed since" in result.error
        assert f.read_text(encoding="utf-8") == "someone else wrote this\n"

    def test_crlf_preserved_for_lf_string_input(self, tmp_path):
        f = tmp_path / "win.txt"
        f.write_bytes(b"line one\r\nline two\r\n")
        result = _run(
            self.tool.run({"path": str(f), "old_string": "line one", "new_string": "FIRST"})
        )
        assert result.success is True
        out = f.read_bytes()
        assert out == b"FIRST\r\nline two\r\n"

    def test_missing_file_refused_with_write_file_hint(self, tmp_path):
        result = _run(
            self.tool.run(
                {"path": str(tmp_path / "ghost.py"), "old_string": "a", "new_string": "b"}
            )
        )
        assert result.success is False
        assert "not found" in result.error.lower()
        assert "write_file" in result.error

    def test_empty_old_string_refused(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("data")
        result = _run(self.tool.run({"path": str(f), "old_string": "", "new_string": "y"}))
        assert result.success is False
        assert "non-empty" in result.error

    def test_non_utf8_file_refused(self, tmp_path):
        f = tmp_path / "latin.txt"
        f.write_bytes(b"caf\xe9")  # latin-1 é, invalid UTF-8
        result = _run(self.tool.run({"path": str(f), "old_string": "caf", "new_string": "tea"}))
        assert result.success is False
        assert "UTF-8" in result.error

    def test_no_temp_leftovers_after_success(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("a = 1\n", encoding="utf-8")
        result = _run(self.tool.run({"path": str(f), "old_string": "1", "new_string": "2"}))
        assert result.success is True
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "clean.py"]
        assert leftovers == []

    def test_oversize_result_refused(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("x", encoding="utf-8")
        result = _run(
            self.tool.run(
                {
                    "path": str(f),
                    "old_string": "x",
                    "new_string": "y" * (_MAX_WRITE_CHARS + 10),
                }
            )
        )
        assert result.success is False
        assert "large" in result.error.lower() or "max" in result.error.lower()

    def test_classification(self):
        from navig.agent.plan_mode import PlanInterceptor
        from navig.agent.toolsets import is_parallel_safe
        from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

        assert "edit_file" in DESTRUCTIVE_TOOLS
        assert is_destructive_tool("edit_file") is True
        assert is_parallel_safe("edit_file") is False
        assert "edit_file" not in PlanInterceptor.READ_ONLY_TOOLS

    def test_registered_in_registry(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        assert "edit_file" in (_AGENT_REGISTRY.available_names() or [])
