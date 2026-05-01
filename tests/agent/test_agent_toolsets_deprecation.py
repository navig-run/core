"""
Batch 40 — navig/agent/toolsets.py + navig/deprecation.py + navig/agent/pattern_analyzer.py
Pure-logic tests, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────
# navig.agent.toolsets
# ─────────────────────────────────────────────────────────────

from navig.agent.toolsets import (
    TOOLSETS,
    NAVIG_CORE_TOOLS,
    PARALLEL_SAFE_TOOLS,
    NEVER_PARALLEL_TOOLS,
    validate_toolset,
    resolve_toolset_names,
    merge_toolsets,
    is_parallel_safe,
)


class TestConstants:
    def test_navig_core_tools_is_frozenset(self):
        assert isinstance(NAVIG_CORE_TOOLS, frozenset)

    def test_navig_core_tools_contains_bash_exec(self):
        assert "bash_exec" in NAVIG_CORE_TOOLS

    def test_navig_core_tools_contains_read_file(self):
        assert "read_file" in NAVIG_CORE_TOOLS

    def test_toolsets_is_dict(self):
        assert isinstance(TOOLSETS, dict)

    def test_toolsets_has_core(self):
        assert "core" in TOOLSETS

    def test_toolsets_has_full(self):
        assert "full" in TOOLSETS

    def test_toolsets_full_is_none(self):
        assert TOOLSETS["full"] is None

    def test_toolsets_core_is_list(self):
        assert isinstance(TOOLSETS["core"], list)

    def test_parallel_safe_is_frozenset(self):
        assert isinstance(PARALLEL_SAFE_TOOLS, frozenset)

    def test_never_parallel_is_frozenset(self):
        assert isinstance(NEVER_PARALLEL_TOOLS, frozenset)

    def test_parallel_safe_has_read_file(self):
        assert "read_file" in PARALLEL_SAFE_TOOLS

    def test_never_parallel_has_bash_exec(self):
        assert "bash_exec" in NEVER_PARALLEL_TOOLS

    def test_sets_disjoint(self):
        # A tool should not appear in both sets
        overlap = PARALLEL_SAFE_TOOLS & NEVER_PARALLEL_TOOLS
        assert len(overlap) == 0


class TestValidateToolset:
    def test_valid_name_does_not_raise(self):
        validate_toolset("core")  # no exception

    def test_valid_full_does_not_raise(self):
        validate_toolset("full")

    def test_invalid_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown toolset"):
            validate_toolset("nonexistent_toolset_xyz")

    def test_error_message_lists_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            validate_toolset("bad")
        assert "core" in str(exc_info.value)


class TestResolveToolsetNames:
    def test_core_returns_list(self):
        result = resolve_toolset_names("core")
        assert isinstance(result, list)

    def test_full_returns_none(self):
        assert resolve_toolset_names("full") is None

    def test_search_returns_search_tool(self):
        result = resolve_toolset_names("search")
        assert "search" in result

    def test_git_returns_git_status(self):
        result = resolve_toolset_names("git")
        assert "git_status" in result

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            resolve_toolset_names("does_not_exist")


class TestMergeToolsets:
    def test_single_toolset_returns_its_tools(self):
        result = merge_toolsets(["search"])
        assert "search" in result
        assert isinstance(result, list)

    def test_two_toolsets_merged_deduplicated(self):
        # core and code both contain bash_exec
        result = merge_toolsets(["core", "code"])
        assert result is not None
        assert result.count("bash_exec") == 1

    def test_full_toolset_returns_none(self):
        result = merge_toolsets(["full"])
        assert result is None

    def test_full_in_list_returns_none(self):
        result = merge_toolsets(["core", "full"])
        assert result is None

    def test_order_preserved(self):
        result = merge_toolsets(["search"])
        # "search" should be the first entry
        assert result[0] == "search"

    def test_unknown_toolset_raises(self):
        with pytest.raises(ValueError):
            merge_toolsets(["nonexistent_xyz"])


class TestIsParallelSafe:
    def test_read_file_is_safe(self):
        assert is_parallel_safe("read_file") is True

    def test_bash_exec_is_not_safe(self):
        assert is_parallel_safe("bash_exec") is False

    def test_write_file_is_not_safe(self):
        assert is_parallel_safe("write_file") is False

    def test_unknown_tool_is_not_safe(self):
        # Not in either set → defaults to False via the implementation
        # (not in NEVER_PARALLEL but also not in PARALLEL_SAFE → returns False)
        assert is_parallel_safe("unknown_tool_xyz_42") is False

    def test_search_is_safe(self):
        assert is_parallel_safe("search") is True

    def test_git_commit_is_not_safe(self):
        assert is_parallel_safe("git_commit") is False


# ─────────────────────────────────────────────────────────────
# navig.deprecation
# ─────────────────────────────────────────────────────────────

from navig.deprecation import (
    deprecated_command,
    deprecation_warning,
    DEPRECATION_MAP,
    get_canonical_command,
)


class TestDeprecatedCommandDecorator:
    def test_decorated_function_still_called(self):
        called = []

        @deprecated_command("navig old cmd", "navig new cmd", show_warning=False)
        def my_cmd():
            called.append(True)
            return 42

        result = my_cmd()
        assert result == 42
        assert called == [True]

    def test_show_warning_triggers_ch_warning(self):
        mock_ch = MagicMock()
        with patch("navig.deprecation.ch", mock_ch):
            @deprecated_command("navig old", "navig new", show_warning=True)
            def cmd():
                return "ok"

            cmd()
            mock_ch.warning.assert_called_once()

    def test_no_warning_when_show_warning_false(self):
        mock_ch = MagicMock()
        with patch("navig.deprecation.ch", mock_ch):
            @deprecated_command("navig old", "navig new", show_warning=False)
            def cmd():
                return "ok"

            cmd()
            mock_ch.warning.assert_not_called()

    def test_warning_message_contains_old_command(self):
        messages = []
        mock_ch = MagicMock()
        mock_ch.warning.side_effect = lambda *args: messages.extend(args)
        with patch("navig.deprecation.ch", mock_ch):
            @deprecated_command("navig host info", "navig host show", show_warning=True)
            def cmd():
                pass

            cmd()

        combined = " ".join(messages)
        assert "navig host info" in combined

    def test_warning_message_contains_new_command(self):
        messages = []
        mock_ch = MagicMock()
        mock_ch.warning.side_effect = lambda *args: messages.extend(args)
        with patch("navig.deprecation.ch", mock_ch):
            @deprecated_command("navig old", "navig new cmd", show_warning=True)
            def cmd():
                pass

            cmd()

        combined = " ".join(messages)
        assert "navig new cmd" in combined

    def test_preserves_function_name(self):
        @deprecated_command("old", "new", show_warning=False)
        def my_function():
            pass

        assert my_function.__name__ == "my_function"

    def test_args_and_kwargs_forwarded(self):
        @deprecated_command("old", "new", show_warning=False)
        def add(a, b, *, extra=0):
            return a + b + extra

        assert add(1, 2, extra=10) == 13


class TestDeprecationWarning:
    def test_calls_ch_warning(self):
        mock_ch = MagicMock()
        with patch("navig.deprecation.ch", mock_ch):
            deprecation_warning("navig old", "navig new")
            mock_ch.warning.assert_called_once()

    def test_warning_contains_old_command(self):
        messages = []
        mock_ch = MagicMock()
        mock_ch.warning.side_effect = lambda *args: messages.extend(args)
        with patch("navig.deprecation.ch", mock_ch):
            deprecation_warning("navig some-old-cmd", "navig new-cmd")
        combined = " ".join(messages)
        assert "navig some-old-cmd" in combined


class TestDeprecationMap:
    def test_map_is_dict(self):
        assert isinstance(DEPRECATION_MAP, dict)

    def test_known_entry_host_info(self):
        assert DEPRECATION_MAP.get("navig host info") == "navig host show"

    def test_known_entry_upload(self):
        assert DEPRECATION_MAP.get("navig upload") == "navig file add"

    def test_known_entry_ls(self):
        assert DEPRECATION_MAP.get("navig ls") == "navig file list"

    def test_all_values_are_strings(self):
        assert all(isinstance(v, str) for v in DEPRECATION_MAP.values())

    def test_all_keys_are_strings(self):
        assert all(isinstance(k, str) for k in DEPRECATION_MAP.keys())


class TestGetCanonicalCommand:
    def test_known_returns_replacement(self):
        result = get_canonical_command("navig host info")
        assert result == "navig host show"

    def test_upload_returns_file_add(self):
        assert get_canonical_command("navig upload") == "navig file add"

    def test_unknown_returns_none(self):
        assert get_canonical_command("navig unknown-xyz-42") is None

    def test_cat_returns_file_show(self):
        assert get_canonical_command("navig cat") == "navig file show"


# ─────────────────────────────────────────────────────────────
# navig.agent.pattern_analyzer
# ─────────────────────────────────────────────────────────────

from navig.agent.pattern_analyzer import PatternAnalyzer, ScoredPattern


@dataclass
class FakeRecord:
    command: str


class TestScoredPattern:
    def test_fields_accessible(self):
        sp = ScoredPattern(sequence=("ls",), occurrences=3, score=3.0)
        assert sp.sequence == ("ls",)
        assert sp.occurrences == 3
        assert sp.score == 3.0


class TestPatternAnalyzer:
    def test_empty_records_returns_empty(self):
        pa = PatternAnalyzer(min_occurrences=1)
        assert pa.score_by_frequency([]) == []

    def test_single_record_below_min_occurrences(self):
        pa = PatternAnalyzer(min_occurrences=2)
        result = pa.score_by_frequency([FakeRecord("ls")])
        assert result == []

    def test_repeated_command_appears_in_results(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [FakeRecord("ls")] * 3
        result = pa.score_by_frequency(records)
        assert len(result) == 1
        assert result[0].sequence == ("ls",)
        assert result[0].occurrences == 3

    def test_min_occurrences_boundary(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [FakeRecord("ls")] * 2
        result = pa.score_by_frequency(records)
        assert len(result) == 1

    def test_sorted_by_score_descending(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord("a"), FakeRecord("a"), FakeRecord("a"),
                   FakeRecord("b"), FakeRecord("b")]
        result = pa.score_by_frequency(records)
        assert result[0].occurrences >= result[1].occurrences

    def test_max_results_limits_output(self):
        pa = PatternAnalyzer(min_occurrences=1, max_results=2)
        records = [FakeRecord(f"cmd{i}") for i in range(5)]
        result = pa.score_by_frequency(records)
        assert len(result) <= 2

    def test_record_without_command_attr_skipped(self):
        pa = PatternAnalyzer(min_occurrences=1)

        class NoCommand:
            pass

        result = pa.score_by_frequency([NoCommand()])
        assert result == []

    def test_whitespace_only_command_skipped(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord("   ")] * 3
        result = pa.score_by_frequency(records)
        assert result == []

    def test_non_string_command_skipped(self):
        pa = PatternAnalyzer(min_occurrences=1)

        @dataclass
        class NumRecord:
            command: int

        records = [NumRecord(42)] * 3
        result = pa.score_by_frequency(records)
        assert result == []

    def test_default_min_occurrences(self):
        pa = PatternAnalyzer()
        assert pa.min_occurrences == 2
        assert pa.max_results == 20
