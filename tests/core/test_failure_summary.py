"""Tests for navig.core.evolution.failure_summary — summarize_check_failure."""
from __future__ import annotations

import pytest

from navig.core.evolution.failure_summary import summarize_check_failure


class TestSummarizeCheckFailure:
    def test_empty_inputs_returns_empty(self):
        assert summarize_check_failure("", "") == ""

    def test_none_inputs_treated_as_empty(self):
        assert summarize_check_failure(None, None) == ""  # type: ignore[arg-type]

    def test_extracts_failed_test_names(self):
        stdout = "FAILED tests/test_foo.py::TestBar::test_baz\n1 failed in 0.5s"
        result = summarize_check_failure(stdout, "")
        assert "test_foo.py::TestBar::test_baz" in result

    def test_extracts_failed_count(self):
        stdout = "3 failed, 10 passed in 2.0s"
        result = summarize_check_failure(stdout, "")
        assert "3" in result

    def test_extracts_traceback_line(self):
        stdout = "collection...\nE   AttributeError: 'NoneType' object has no attribute 'x'"
        result = summarize_check_failure(stdout, "")
        assert "AttributeError" in result

    def test_fallback_to_first_stderr_line(self):
        result = summarize_check_failure("", "some error here\nmore details")
        assert "some error here" in result

    def test_always_ends_with_suggested_next_step(self):
        result = summarize_check_failure("FAILED tests/x.py::T::m", "")
        assert "Suggested next step" in result

    def test_limits_failing_targets_to_three(self):
        lines = "\n".join(f"FAILED tests/test_{i}.py::T::m" for i in range(10))
        result = summarize_check_failure(lines, "")
        # Should show first 3; verify the summary doesn't have 10 FAILED references
        count = result.count("test_")
        assert count <= 4  # up to 3 in preview + 1 in failed tests count line

    def test_uses_stderr_when_stdout_empty(self):
        stderr = "2 failed\nFAILED tests/test_x.py::T::m"
        result = summarize_check_failure("", stderr)
        assert "2" in result

    def test_combined_stdout_and_stderr(self):
        stdout = "FAILED tests/a.py::T::m"
        stderr = "1 failed"
        result = summarize_check_failure(stdout, stderr)
        assert "a.py" in result
