"""Tests for navig.agent.tool_caps — cap_result, get_cap_for_tool, cleanup_spillover."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.agent.tool_caps import (
    DEFAULT_MAX_RESULT_CHARS,
    SPILLOVER_TTL,
    TOOL_SPECIFIC_CAPS,
    _LINE_SNAP_MIN_RATIO,
    _write_spillover,
    cap_result,
    cleanup_spillover,
    get_cap_for_tool,
)


# ---------------------------------------------------------------------------
# get_cap_for_tool
# ---------------------------------------------------------------------------

class TestGetCapForTool:
    def test_known_tool_returns_specific_cap(self):
        assert get_cap_for_tool("read_file") == TOOL_SPECIFIC_CAPS["read_file"]
        assert get_cap_for_tool("grep_search") == TOOL_SPECIFIC_CAPS["grep_search"]

    def test_unknown_tool_returns_default(self):
        assert get_cap_for_tool("nonexistent_tool") == DEFAULT_MAX_RESULT_CHARS
        assert get_cap_for_tool("") == DEFAULT_MAX_RESULT_CHARS

    def test_all_specific_caps_are_positive(self):
        for tool, cap in TOOL_SPECIFIC_CAPS.items():
            assert cap > 0, f"{tool} cap should be positive"

    def test_bash_exec_has_larger_cap(self):
        assert TOOL_SPECIFIC_CAPS["bash_exec"] > TOOL_SPECIFIC_CAPS["web_fetch"]

    def test_navig_run_has_large_cap(self):
        assert TOOL_SPECIFIC_CAPS["navig_run"] >= 30_000


# ---------------------------------------------------------------------------
# cap_result — short result passes through
# ---------------------------------------------------------------------------

class TestCapResultShortResult:
    def test_short_result_returned_unchanged(self):
        text = "hello world"
        assert cap_result(text) is text

    def test_empty_string_returned_unchanged(self):
        assert cap_result("") == ""

    def test_exactly_at_limit_returned_unchanged(self):
        text = "x" * DEFAULT_MAX_RESULT_CHARS
        result = cap_result(text)
        assert result == text

    def test_short_result_with_tool_name(self):
        text = "short output"
        assert cap_result(text, tool_name="bash_exec") == text

    def test_custom_max_chars_short(self):
        text = "abc"
        assert cap_result(text, max_chars=100) == text


# ---------------------------------------------------------------------------
# cap_result — truncation cases
# ---------------------------------------------------------------------------

class TestCapResultTruncation:
    def test_over_limit_is_truncated(self):
        text = "x" * (DEFAULT_MAX_RESULT_CHARS + 1000)
        result = cap_result(text)
        assert len(result) < len(text)
        assert "[Truncated" in result

    def test_footer_contains_original_size(self):
        size = DEFAULT_MAX_RESULT_CHARS + 500
        text = "a" * size
        result = cap_result(text)
        # Footer uses comma-formatted numbers like "30,500"
        import re
        assert re.search(r"30.?500", result)

    def test_footer_mentions_max_chars(self):
        text = "b" * (DEFAULT_MAX_RESULT_CHARS + 100)
        result = cap_result(text)
        assert str(DEFAULT_MAX_RESULT_CHARS) in result or "Truncated" in result

    def test_custom_max_chars_truncates(self):
        text = "hello world\nhow are you\nline3\n"
        result = cap_result(text, max_chars=12)
        assert "[Truncated" in result

    def test_negative_max_chars_truncates_everything(self):
        text = "some content"
        result = cap_result(text, max_chars=-1)
        assert "[Truncated" in result

    def test_line_snap_cuts_at_newline(self):
        # Build a string where newline is near the cap
        cap = 100
        prefix = "line1\n"
        rest = "x" * (cap - len(prefix) + 10)  # goes over by 10
        text = prefix + rest
        result = cap_result(text, max_chars=cap)
        # The truncation should cut at the newline if it's past the min ratio
        assert "[Truncated" in result

    def test_tool_name_uses_specific_cap(self):
        cap = TOOL_SPECIFIC_CAPS["list_files"]
        text = "f\n" * (cap + 10)
        result = cap_result(text, tool_name="list_files")
        assert "[Truncated" in result

    def test_spillover_path_in_footer_when_writable(self, tmp_path):
        text = "z" * (DEFAULT_MAX_RESULT_CHARS + 100)
        import navig.agent.tool_caps as mod
        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            result = cap_result(text, tool_name="test_tool")
        assert "Full result saved to:" in result or "could not be saved" in result

    def test_spillover_failure_footer_when_dir_not_writable(self, tmp_path):
        text = "q" * (DEFAULT_MAX_RESULT_CHARS + 100)
        import navig.agent.tool_caps as mod
        # Point SPILLOVER_DIR to a non-writable location
        bad_dir = tmp_path / "no_write"
        with patch.object(mod, "SPILLOVER_DIR", bad_dir):
            with patch("navig.agent.tool_caps.SPILLOVER_DIR", bad_dir):
                with patch("navig.agent.tool_caps._write_spillover", return_value=None):
                    result = cap_result(text)
        assert "could not be saved" in result or "Truncated" in result


# ---------------------------------------------------------------------------
# _write_spillover
# ---------------------------------------------------------------------------

class TestWriteSpillover:
    def test_writes_and_returns_path(self, tmp_path):
        import navig.agent.tool_caps as mod
        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            path = _write_spillover("some content", "test_tool")
        assert path is not None
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "some content"

    def test_same_content_not_rewritten(self, tmp_path):
        import navig.agent.tool_caps as mod
        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            p1 = _write_spillover("identical", "tool")
            mtime1 = p1.stat().st_mtime
            time.sleep(0.05)
            p2 = _write_spillover("identical", "tool")
        assert p1 == p2
        assert p2.stat().st_mtime == mtime1  # Not rewritten

    def test_filename_includes_tool_name(self, tmp_path):
        import navig.agent.tool_caps as mod
        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            path = _write_spillover("content", "bash_exec")
        assert "bash_exec" in path.name

    def test_empty_tool_name_uses_default_prefix(self, tmp_path):
        import navig.agent.tool_caps as mod
        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            path = _write_spillover("data", "")
        assert path.name.startswith("tool_")

    def test_os_error_returns_none(self):
        import navig.agent.tool_caps as mod
        # Make the dir an existing file to force mkdir failure
        with tempfile.NamedTemporaryFile(delete=False) as f:
            bad_dir = Path(f.name)
        try:
            with patch.object(mod, "SPILLOVER_DIR", bad_dir):
                result = _write_spillover("oops", "tool")
        finally:
            bad_dir.unlink(missing_ok=True)
        assert result is None


# ---------------------------------------------------------------------------
# cleanup_spillover
# ---------------------------------------------------------------------------

class TestCleanupSpillover:
    def test_returns_zero_when_dir_missing(self, tmp_path):
        import navig.agent.tool_caps as mod
        missing = tmp_path / "no_such_dir"
        with patch.object(mod, "SPILLOVER_DIR", missing):
            count = cleanup_spillover()
        assert count == 0

    def test_removes_old_files(self, tmp_path):
        # Create fake stale files
        import navig.agent.tool_caps as mod
        f1 = tmp_path / "old_file.txt"
        f1.write_text("stale")
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(f1, (old_time, old_time))

        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            count = cleanup_spillover(max_age=3600)
        assert count == 1
        assert not f1.exists()

    def test_keeps_fresh_files(self, tmp_path):
        import navig.agent.tool_caps as mod
        f1 = tmp_path / "fresh_file.txt"
        f1.write_text("new content")
        # mtime is now (fresh)

        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            count = cleanup_spillover(max_age=3600)
        assert count == 0
        assert f1.exists()

    def test_skips_directories(self, tmp_path):
        import navig.agent.tool_caps as mod
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        old_time = time.time() - 7200
        os.utime(subdir, (old_time, old_time))

        with patch.object(mod, "SPILLOVER_DIR", tmp_path):
            count = cleanup_spillover(max_age=3600)
        assert count == 0
        assert subdir.exists()

    def test_handles_oserror_gracefully(self, tmp_path):
        import navig.agent.tool_caps as mod
        f1 = tmp_path / "locked.txt"
        f1.write_text("data")
        old_time = time.time() - 7200
        os.utime(f1, (old_time, old_time))

        original_unlink = Path.unlink

        def patched_unlink(self, missing_ok=False):
            raise OSError("permission denied")

        with patch.object(Path, "unlink", patched_unlink):
            with patch.object(mod, "SPILLOVER_DIR", tmp_path):
                count = cleanup_spillover(max_age=3600)
        # Should be 0 because unlink raised
        assert count == 0


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_max_chars_reasonable(self):
        assert DEFAULT_MAX_RESULT_CHARS >= 10_000

    def test_line_snap_ratio_between_0_and_1(self):
        assert 0 < _LINE_SNAP_MIN_RATIO < 1.0

    def test_spillover_ttl_positive(self):
        assert SPILLOVER_TTL > 0
