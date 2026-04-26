"""Tests for navig.ui.panels — render_primary_state, render_explanation, render_metrics_panel."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.ui.panels as panels_mod
from navig.ui.models import CauseScore, Metric
from navig.ui.panels import render_explanation, render_metrics_panel, render_primary_state


class TestRenderPrimaryState:
    def test_does_not_raise(self) -> None:
        mock_console = MagicMock()
        with patch.object(panels_mod, "console", mock_console):
            render_primary_state("Running", "▶", "all systems go")  # must not raise

    def test_calls_console_print(self) -> None:
        mock_console = MagicMock()
        with patch.object(panels_mod, "console", mock_console):
            render_primary_state("Running", "▶", "details")
        mock_console.print.assert_called_once()

    def test_never_raises_even_when_console_fails(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = Exception("exploded")
        with patch.object(panels_mod, "console", bad):
            render_primary_state("X", "!", "detail")  # must not propagate

    def test_includes_hint_when_provided(self) -> None:
        mock_console = MagicMock()
        with patch.object(panels_mod, "console", mock_console):
            render_primary_state("Running", "▶", "detail", hint="press Ctrl+C to cancel")
        call_text = str(mock_console.print.call_args)
        assert "press Ctrl+C" in call_text

    def test_no_hint_when_not_provided(self) -> None:
        mock_console = MagicMock()
        with patch.object(panels_mod, "console", mock_console):
            render_primary_state("Running", "▶", "detail")
        # Only one \n section (no hint line)
        call_text = str(mock_console.print.call_args)
        assert "press" not in call_text


class TestRenderExplanation:
    def test_empty_causes_no_output(self) -> None:
        mock_console = MagicMock()
        with patch.object(panels_mod, "console", mock_console):
            render_explanation([])
        mock_console.print.assert_not_called()

    def test_prints_title_and_causes(self) -> None:
        mock_console = MagicMock()
        causes = [CauseScore(confidence=80, description="High load")]
        with patch.object(panels_mod, "console", mock_console):
            render_explanation(causes, title="Root Cause")
        mock_console.print.assert_called_once()
        call_text = str(mock_console.print.call_args)
        assert "Root Cause" in call_text

    def test_never_raises_on_console_error(self) -> None:
        bad = MagicMock()
        bad.print.side_effect = RuntimeError("gone")
        causes = [CauseScore(confidence=50, description="Unknown")]
        with patch.object(panels_mod, "console", bad):
            render_explanation(causes)  # must not propagate


class TestRenderMetricsPanel:
    def test_does_not_raise_on_empty_list(self) -> None:
        with patch("navig.ui.bars.render_metric_bars") as mock_rm:
            render_metrics_panel([])  # must not raise

    def test_delegates_to_render_metric_bars(self) -> None:
        metrics = [Metric(label="CPU", value="80%", bar_fill=0.8)]
        with patch("navig.ui.bars.render_metric_bars") as mock_rm:
            render_metrics_panel(metrics, title="System")
        mock_rm.assert_called_once()

    def test_never_raises_even_on_import_error(self) -> None:
        import sys
        saved = sys.modules.pop("navig.ui.bars", None)
        try:
            render_metrics_panel([Metric(label="x", value="0", bar_fill=0.0)])
        finally:
            if saved is not None:
                sys.modules["navig.ui.bars"] = saved
