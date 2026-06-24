"""Tests for navig.ui.bars — _make_bar, render_metric_bars, render_sparklines."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.ui.bars as bars_mod
from navig.ui.bars import _make_bar, render_metric_bars, render_sparklines
from navig.ui.models import Metric


# ---------------------------------------------------------------------------
# _make_bar
# ---------------------------------------------------------------------------

class TestMakeBar:
    def test_zero_fill_all_empty(self) -> None:
        filled, empty = _make_bar(0.0, width=10)
        assert len(filled) == 0
        assert len(empty) == 10

    def test_full_fill_all_filled(self) -> None:
        filled, empty = _make_bar(1.0, width=10)
        assert len(filled) == 10
        assert len(empty) == 0

    def test_half_fill(self) -> None:
        filled, empty = _make_bar(0.5, width=10)
        assert len(filled) == 5
        assert len(empty) == 5

    def test_total_length_equals_width(self) -> None:
        for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
            filled, empty = _make_bar(v, width=20)
            assert len(filled) + len(empty) == 20

    def test_clamps_above_one(self) -> None:
        filled, empty = _make_bar(1.5, width=10)
        assert len(filled) == 10

    def test_clamps_below_zero(self) -> None:
        filled, empty = _make_bar(-0.5, width=10)
        assert len(filled) == 0

    def test_safe_mode_uses_ascii(self) -> None:
        with patch.object(bars_mod, "SAFE_MODE", True):
            filled, empty = _make_bar(1.0, width=5)
        assert all(c == "#" for c in filled)

    def test_rich_mode_uses_block_chars(self) -> None:
        with patch.object(bars_mod, "SAFE_MODE", False):
            filled, empty = _make_bar(1.0, width=5)
        assert all(c == "█" for c in filled)


# ---------------------------------------------------------------------------
# render_metric_bars
# ---------------------------------------------------------------------------

class TestRenderMetricBars:
    def _metric(self, label="CPU", value="80%", fill=0.8, spark=None) -> Metric:
        return Metric(label=label, value=value, bar_fill=fill, sparkline=spark, color="cyan")

    def test_does_not_raise_on_empty_list(self) -> None:
        mock_console = MagicMock()
        with patch.object(bars_mod, "console", mock_console):
            render_metric_bars([])  # should return silently
        mock_console.print.assert_not_called()

    def test_prints_title_and_metric(self) -> None:
        mock_console = MagicMock()
        with patch.object(bars_mod, "console", mock_console):
            render_metric_bars([self._metric()], title="System")
        # At minimum the title and one metric row printed
        assert mock_console.print.call_count >= 2

    def test_never_raises_even_if_console_explodes(self) -> None:
        bad_console = MagicMock()
        bad_console.print.side_effect = Exception("boom")
        with patch.object(bars_mod, "console", bad_console):
            render_metric_bars([self._metric()])  # must not propagate

    def test_renders_sparkline_when_present(self) -> None:
        mock_console = MagicMock()
        with patch.object(bars_mod, "console", mock_console):
            render_metric_bars([self._metric(spark="▁▂▃▄")])
        calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "▁▂▃▄" in calls


# ---------------------------------------------------------------------------
# render_sparklines
# ---------------------------------------------------------------------------

class TestRenderSparklines:
    def test_skips_metrics_without_sparkline(self) -> None:
        mock_console = MagicMock()
        m = Metric(label="CPU", value="80%", bar_fill=0.8, sparkline=None)
        with patch.object(bars_mod, "console", mock_console):
            render_sparklines([m])
        mock_console.print.assert_not_called()

    def test_renders_metrics_with_sparkline(self) -> None:
        mock_console = MagicMock()
        m = Metric(label="Mem", value="50%", bar_fill=0.5, sparkline="▂▃▄▅")
        with patch.object(bars_mod, "console", mock_console):
            render_sparklines([m], title="Trend")
        assert mock_console.print.call_count >= 2

    def test_never_raises_on_console_error(self) -> None:
        bad_console = MagicMock()
        bad_console.print.side_effect = RuntimeError("gone")
        m = Metric(label="x", value="1%", bar_fill=0.1, sparkline="▁")
        with patch.object(bars_mod, "console", bad_console):
            render_sparklines([m])  # must not propagate
