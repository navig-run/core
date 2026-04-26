"""Tests for navig.ui.summary — render_next_step, render_summary, render_ai_response."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.ui.summary as summary_mod
from navig.ui.models import SummaryResult
from navig.ui.summary import render_ai_response, render_next_step, render_summary


def _result(root="High load", rec="Restart service", conf=75, prompt=None) -> SummaryResult:
    return SummaryResult(
        root_cause=root, recommendation=rec, confidence=conf, action_prompt=prompt
    )


class TestRenderNextStep:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_next_step("navig host restart")

    def test_calls_console_print(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_next_step("navig host restart")
        mock_console.print.assert_called_once()

    def test_command_in_output(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_next_step("navig db list")
        call_text = str(mock_console.print.call_args)
        assert "navig db list" in call_text

    def test_custom_label_in_output(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_next_step("navig run check", label="Run this")
        call_text = str(mock_console.print.call_args)
        assert "Run this" in call_text

    def test_never_raises_on_console_error(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = Exception("boom")
        with patch.object(summary_mod, "console", bad):
            render_next_step("command")  # must not propagate


class TestRenderSummary:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_summary(_result())

    def test_prints_title(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_summary(_result(), title="My Summary")
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "My Summary" in calls

    def test_prints_root_cause(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_summary(_result(root="Memory leak"))
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Memory leak" in calls

    def test_prints_recommendation(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_summary(_result(rec="Clear cache"))
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Clear cache" in calls

    def test_renders_action_prompt_when_set(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_summary(_result(prompt="navig restart"))
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "navig restart" in calls

    def test_never_raises_on_console_error(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = RuntimeError("gone")
        with patch.object(summary_mod, "console", bad):
            render_summary(_result())  # must not propagate


class TestRenderAiResponse:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_ai_response("Hello from AI")

    def test_prints_each_line(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_ai_response("Line 1\nLine 2\nLine 3")
        assert mock_console.print.call_count >= 3

    def test_prints_title_when_provided(self) -> None:
        mock_console = MagicMock()
        with patch.object(summary_mod, "console", mock_console):
            render_ai_response("response", title="AI says")
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "AI says" in calls

    def test_never_raises_on_console_error(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = Exception("gone")
        with patch.object(summary_mod, "console", bad):
            render_ai_response("anything")  # must not propagate
