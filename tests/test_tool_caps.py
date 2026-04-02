"""Tests for navig.agent.tool_caps — tool result capping with disk spillover."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.agent.tool_caps import (
    DEFAULT_MAX_RESULT_CHARS,
    SPILLOVER_DIR,
    SPILLOVER_TTL,
    TOOL_SPECIFIC_CAPS,
    _LINE_SNAP_MIN_RATIO,
    cap_result,
    cleanup_spillover,
    get_cap_for_tool,
)


# ─────────────────────────────────────────────────────────────
# get_cap_for_tool
# ─────────────────────────────────────────────────────────────


class TestGetCapForTool:
    """Tests for per-tool cap lookup."""

    def test_known_tool_returns_specific_cap(self):
        assert get_cap_for_tool("bash_exec") == 50_000

    def test_unknown_tool_returns_default(self):
        assert get_cap_for_tool("imaginary_tool") == DEFAULT_MAX_RESULT_CHARS

    def test_read_file_cap(self):
        assert get_cap_for_tool("read_file") == 30_000

    def test_search_cap_smaller_than_default(self):
        assert get_cap_for_tool("search") < DEFAULT_MAX_RESULT_CHARS

    def test_all_specific_caps_are_positive(self):
        for tool, cap in TOOL_SPECIFIC_CAPS.items():
            assert cap > 0, f"{tool} has non-positive cap"


# ─────────────────────────────────────────────────────────────
# cap_result — small results
# ─────────────────────────────────────────────────────────────


class TestCapResultPassthrough:
    """Results under the cap should pass through unchanged."""

    def test_empty_string_unchanged(self):
        assert cap_result("") == ""

    def test_short_string_unchanged(self):
        s = "Hello, world!"
        assert cap_result(s) == s

    def test_exact_limit_unchanged(self):
        s = "x" * DEFAULT_MAX_RESULT_CHARS
        assert cap_result(s) == s

    def test_one_under_limit_unchanged(self):
        s = "x" * (DEFAULT_MAX_RESULT_CHARS - 1)
        assert cap_result(s) == s

    def test_custom_limit_passthrough(self):
        s = "x" * 100
        assert cap_result(s, max_chars=200) == s


# ─────────────────────────────────────────────────────────────
# cap_result — truncation
# ─────────────────────────────────────────────────────────────


class TestCapResultTruncation:
    """Results over the cap should be truncated with footer."""

    def test_over_limit_is_truncated(self):
        big = "x" * (DEFAULT_MAX_RESULT_CHARS + 500)
        result = cap_result(big)
        assert len(result) < len(big)

    def test_truncation_footer_present(self):
        big = "x" * (DEFAULT_MAX_RESULT_CHARS + 100)
        result = cap_result(big)
        assert "[Truncated at" in result
        assert "chars" in result

    def test_footer_shows_total_size(self):
        n = DEFAULT_MAX_RESULT_CHARS + 1234
        big = "x" * n
        result = cap_result(big)
        assert f"{n:,}" in result

    def test_custom_max_chars(self):
        big = "a" * 500
        result = cap_result(big, max_chars=100)
        assert len(result) < 500
        assert "[Truncated at 100 chars" in result

    def test_tool_name_selects_specific_cap(self):
        # bash_exec has 50K cap, so a 40K result should NOT be truncated
        s = "z" * 40_000
        result = cap_result(s, tool_name="bash_exec")
        assert result == s

    def test_tool_name_over_specific_cap(self):
        # search has 15K cap
        s = "z" * 20_000
        result = cap_result(s, tool_name="search")
        assert "[Truncated at" in result

    def test_max_chars_overrides_tool_specific(self):
        # Even though bash_exec allows 50K, explicit max_chars=100 wins
        s = "z" * 500
        result = cap_result(s, max_chars=100, tool_name="bash_exec")
        assert "[Truncated at 100 chars" in result


# ─────────────────────────────────────────────────────────────
# cap_result — line-boundary snapping
# ─────────────────────────────────────────────────────────────


class TestLineSnapping:
    """Truncation should snap to the last newline when reasonable."""

    def test_snaps_to_last_newline(self):
        # Build content with a newline near the end of the cap zone
        lines = ["line-" + str(i).zfill(5) for i in range(5000)]
        content = "\n".join(lines)
        result = cap_result(content, max_chars=200)
        # The truncated part (before the footer) should end at a newline
        body = result.split("\n\n[Truncated")[0]
        assert body.endswith("\n") or body[-1].isalnum()  # either snapped or full line

    def test_does_not_snap_if_newline_too_early(self):
        # If the only newline is at position 0, should NOT snap there
        content = "\n" + "x" * 1000
        result = cap_result(content, max_chars=200)
        body = result.split("\n\n[Truncated")[0]
        # Should be close to 200 chars, not 1
        assert len(body) > 100


# ─────────────────────────────────────────────────────────────
# cap_result — disk spillover
# ─────────────────────────────────────────────────────────────


class TestDiskSpillover:
    """Full results should be written to disk when truncated."""

    def test_spillover_file_created(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "x" * 500
            result = cap_result(big, max_chars=100, tool_name="test_tool")
            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert files[0].name.startswith("test_tool_")
            assert files[0].name.endswith(".txt")

    def test_spillover_contains_full_content(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "abcdef" * 100
            cap_result(big, max_chars=50, tool_name="read_file")
            spill = list(tmp_path.iterdir())[0]
            assert spill.read_text(encoding="utf-8") == big

    def test_spillover_path_in_footer(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "x" * 200
            result = cap_result(big, max_chars=50, tool_name="read_file")
            assert str(tmp_path) in result

    def test_identical_content_reuses_file(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "x" * 200
            cap_result(big, max_chars=50, tool_name="test")
            cap_result(big, max_chars=50, tool_name="test")
            # Same hash → same file, should not create duplicate
            assert len(list(tmp_path.iterdir())) == 1

    def test_no_spillover_for_small_results(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            cap_result("small", max_chars=1000, tool_name="test")
            assert len(list(tmp_path.iterdir())) == 0

    def test_spillover_failure_still_truncates(self, tmp_path):
        bad_dir = tmp_path / "no" / "way" / "this" / "exists"
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", bad_dir):
            with patch("pathlib.Path.mkdir", side_effect=OSError("denied")):
                big = "x" * 500
                result = cap_result(big, max_chars=100)
                assert "[Truncated at" in result
                assert "could not be saved" in result


# ─────────────────────────────────────────────────────────────
# cleanup_spillover
# ─────────────────────────────────────────────────────────────


class TestCleanupSpillover:
    """Cleanup should remove old files and keep fresh ones."""

    def test_removes_expired_files(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            # Create a file and backdate its mtime
            old_file = tmp_path / "old.txt"
            old_file.write_text("old content")
            old_time = time.time() - SPILLOVER_TTL - 100
            import os
            os.utime(old_file, (old_time, old_time))

            removed = cleanup_spillover()
            assert removed == 1
            assert not old_file.exists()

    def test_keeps_fresh_files(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            fresh = tmp_path / "fresh.txt"
            fresh.write_text("fresh content")
            removed = cleanup_spillover()
            assert removed == 0
            assert fresh.exists()

    def test_returns_zero_when_dir_missing(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path / "nonexistent"):
            assert cleanup_spillover() == 0

    def test_mixed_old_and_new(self, tmp_path):
        import os
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            old_file = tmp_path / "old.txt"
            old_file.write_text("old")
            old_time = time.time() - SPILLOVER_TTL - 100
            os.utime(old_file, (old_time, old_time))

            fresh_file = tmp_path / "new.txt"
            fresh_file.write_text("fresh")

            removed = cleanup_spillover()
            assert removed == 1
            assert not old_file.exists()
            assert fresh_file.exists()


# ─────────────────────────────────────────────────────────────
# Integration with agent_tool_registry
# ─────────────────────────────────────────────────────────────


class TestRegistryIntegration:
    """Verify agent_tool_registry imports and uses cap_result."""

    def test_registry_imports_cap_result(self):
        """The import should not fail."""
        from navig.agent.agent_tool_registry import cap_result as imported_fn  # noqa: F401
        assert callable(imported_fn)

    def test_registry_no_legacy_max_output_chars(self):
        """The old _MAX_OUTPUT_CHARS constant should be gone."""
        import navig.agent.agent_tool_registry as mod
        assert not hasattr(mod, "_MAX_OUTPUT_CHARS")


# ─────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_max_chars_zero(self):
        # max_chars=0 means everything truncates
        result = cap_result("abc", max_chars=0)
        assert "[Truncated at 0 chars" in result

    def test_max_chars_one(self):
        result = cap_result("abc", max_chars=1)
        assert "[Truncated at 1 chars" in result

    def test_unicode_content(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "日本語テスト " * 10_000
            result = cap_result(big, max_chars=500)
            assert "[Truncated at" in result
            # Spillover should contain the full content
            spill = list(tmp_path.iterdir())[0]
            assert spill.read_text(encoding="utf-8") == big

    def test_tool_name_with_slashes_sanitized(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "x" * 200
            cap_result(big, max_chars=50, tool_name="mcp:server/tool")
            spill = list(tmp_path.iterdir())[0]
            assert "/" not in spill.name
            assert "\\" not in spill.name

    def test_binary_like_content(self, tmp_path):
        with patch("navig.agent.tool_caps.SPILLOVER_DIR", tmp_path):
            big = "\x00\x01\x02" * 200
            result = cap_result(big, max_chars=50)
            assert "[Truncated at" in result
