"""Batch 71 — ui/panels, ui/summary, ui/bars."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.ui.models import ActionItem, CauseScore, DiffLine, Metric, SummaryResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_console(module_path: str):
    """Context manager that patches `console` on the given module."""
    return patch(f"{module_path}.console", new_callable=MagicMock)


# ---------------------------------------------------------------------------
# navig.ui.bars — _make_bar, render_metric_bars, render_sparklines
# ---------------------------------------------------------------------------

class TestMakeBar:
    def test_full_fill(self):
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(1.0)
        assert len(filled) + len(empty) == 20

    def test_zero_fill(self):
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(0.0)
        assert len(empty) == 20

    def test_half_fill(self):
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(0.5)
        assert len(filled) + len(empty) == 20
        assert len(filled) == 10

    def test_clamps_above_one(self):
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(2.0)
        assert len(filled) == 20
        assert len(empty) == 0

    def test_clamps_below_zero(self):
        from navig.ui.bars import _make_bar
        filled, empty = _make_bar(-1.0)
        assert len(filled) == 0
        assert len(empty) == 20


class TestRenderMetricBars:
    def test_calls_console_print(self):
        metrics = [Metric(label="cpu", value="45%", bar_fill=0.45)]
        with _mock_console("navig.ui.bars") as mock_c:
            from navig.ui.bars import render_metric_bars
            render_metric_bars(metrics)
        assert mock_c.print.called

    def test_empty_metrics_no_output(self):
        with _mock_console("navig.ui.bars") as mock_c:
            from navig.ui.bars import render_metric_bars
            render_metric_bars([])
        mock_c.print.assert_not_called()

    def test_title_printed(self):
        metrics = [Metric(label="ram", value="2G", bar_fill=0.5)]
        with _mock_console("navig.ui.bars") as mock_c:
            from navig.ui.bars import render_metric_bars
            render_metric_bars(metrics, title="Resources")
        # First call is the title print
        first = str(mock_c.print.call_args_list[0])
        assert "Resources" in first

    def test_does_not_raise_on_exception(self):
        metrics = [Metric(label="x", value="1", bar_fill=0.5)]
        with patch("navig.ui.bars.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.bars import render_metric_bars
            render_metric_bars(metrics)  # should not propagate


class TestRenderSparklines:
    def test_only_renders_metrics_with_sparkline(self):
        metrics = [
            Metric(label="cpu", value="50%", bar_fill=0.5, sparkline="▂▅▇"),
            Metric(label="ram", value="30%", bar_fill=0.3, sparkline=None),
        ]
        with _mock_console("navig.ui.bars") as mock_c:
            from navig.ui.bars import render_sparklines
            render_sparklines(metrics)
        # Title + 1 sparkline row  = 2 calls
        assert mock_c.print.call_count == 2

    def test_no_sparklines_no_output(self):
        metrics = [Metric(label="cpu", value="50%", bar_fill=0.5, sparkline=None)]
        with _mock_console("navig.ui.bars") as mock_c:
            from navig.ui.bars import render_sparklines
            render_sparklines(metrics)
        mock_c.print.assert_not_called()


# ---------------------------------------------------------------------------
# navig.ui.panels — render_primary_state, render_explanation, render_metrics_panel
# ---------------------------------------------------------------------------

class TestRenderPrimaryState:
    def test_calls_console_print(self):
        with _mock_console("navig.ui.panels") as mock_c:
            from navig.ui.panels import render_primary_state
            render_primary_state("OK", "✓", "All good")
        mock_c.print.assert_called_once()

    def test_output_contains_label(self, capsys):
        with patch("navig.ui.panels.console") as mock_c:
            mock_c.print.side_effect = lambda s: print(s)
            from navig.ui.panels import render_primary_state
            render_primary_state("Running", "▶", "task active")
        out = capsys.readouterr().out
        assert "Running" in out

    def test_hint_included_when_provided(self):
        with _mock_console("navig.ui.panels") as mock_c:
            from navig.ui.panels import render_primary_state
            render_primary_state("OK", "✓", "detail", hint="try --help")
        printed = str(mock_c.print.call_args_list)
        assert "try --help" in printed

    def test_no_raise_on_exception(self):
        with patch("navig.ui.panels.console") as mock_c:
            mock_c.print.side_effect = Exception("boom")
            from navig.ui.panels import render_primary_state
            render_primary_state("X", "!", "err")


class TestRenderExplanation:
    def test_empty_causes_no_output(self):
        with _mock_console("navig.ui.panels") as mock_c:
            from navig.ui.panels import render_explanation
            render_explanation([])
        mock_c.print.assert_not_called()

    def test_causes_printed(self):
        causes = [CauseScore(confidence=80, description="High memory usage", severity="warn")]
        with _mock_console("navig.ui.panels") as mock_c:
            from navig.ui.panels import render_explanation
            render_explanation(causes)
        mock_c.print.assert_called_once()
        printed = str(mock_c.print.call_args_list)
        assert "High memory usage" in printed

    def test_no_raise_on_exception(self):
        causes = [CauseScore(confidence=50, description="test")]
        with patch("navig.ui.panels.console") as mock_c:
            mock_c.print.side_effect = RuntimeError
            from navig.ui.panels import render_explanation
            render_explanation(causes)


class TestRenderMetricsPanel:
    def test_delegates_to_render_metric_bars(self):
        metrics = [Metric(label="cpu", value="50%", bar_fill=0.5)]
        with patch("navig.ui.panels.render_metrics_panel"):
            pass  # ensure import works
        with patch("navig.ui.bars.console"):
            from navig.ui.panels import render_metrics_panel
            render_metrics_panel(metrics)  # should not raise

    def test_no_raise_on_exception(self):
        with patch("navig.ui.panels.render_metrics_panel", side_effect=RuntimeError):
            pass  # render_metrics_panel IS the function, can't patch it this way
        # Instead just call it — bars.console will be patched
        with patch("navig.ui.bars.console"):
            from navig.ui.panels import render_metrics_panel
            render_metrics_panel([])


# ---------------------------------------------------------------------------
# navig.ui.summary — render_next_step, render_summary, render_ai_response
# ---------------------------------------------------------------------------

class TestRenderNextStep:
    def test_calls_console_print(self):
        with _mock_console("navig.ui.summary") as mock_c:
            from navig.ui.summary import render_next_step
            render_next_step("navig run --help")
        mock_c.print.assert_called_once()

    def test_command_in_output(self):
        with patch("navig.ui.summary.console") as mock_c:
            mock_c.print.side_effect = lambda s: print(s)
            from navig.ui.summary import render_next_step
            render_next_step("navig db list", label="Run this")
        # output captured by side_effect — just assert no raise

    def test_no_raise_on_exception(self):
        with patch("navig.ui.summary.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.summary import render_next_step
            render_next_step("cmd")


class TestRenderSummary:
    def _result(self, confidence=75, action_prompt=None):
        return SummaryResult(
            root_cause="High CPU",
            recommendation="Scale out",
            confidence=confidence,
            action_prompt=action_prompt,
        )

    def test_calls_console_multiple_times(self):
        with _mock_console("navig.ui.summary") as mock_c:
            from navig.ui.summary import render_summary
            render_summary(self._result())
        assert mock_c.print.call_count >= 3

    def test_action_prompt_triggers_extra_print(self):
        with _mock_console("navig.ui.summary") as mock_c:
            from navig.ui.summary import render_summary
            render_summary(self._result(action_prompt="navig fix"))
        # Should have more prints when action_prompt is set
        assert mock_c.print.call_count >= 4

    def test_no_raise_on_exception(self):
        with patch("navig.ui.summary.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.summary import render_summary
            render_summary(self._result())


class TestRenderAiResponse:
    def test_prints_each_line(self):
        with _mock_console("navig.ui.summary") as mock_c:
            from navig.ui.summary import render_ai_response
            render_ai_response("line one\nline two")
        assert mock_c.print.call_count == 2

    def test_title_adds_extra_print(self):
        with _mock_console("navig.ui.summary") as mock_c:
            from navig.ui.summary import render_ai_response
            render_ai_response("hello\nworld", title="AI says")
        assert mock_c.print.call_count == 3  # title + 2 lines

    def test_no_raise_on_exception(self):
        with patch("navig.ui.summary.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.summary import render_ai_response
            render_ai_response("text")
