"""Batch 71 — ui/panels, ui/summary, ui/bars."""
from __future__ import annotations

import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from rich.console import Console

from navig.ui.models import CauseScore, Metric, SummaryResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_console(module_path: str):
    """Context manager that patches `console` on the given module."""
    return patch(f"{module_path}.console", new_callable=MagicMock)


@contextmanager
def _rendered(module_path: str):
    """Swap in a REAL Rich console and yield what it actually renders.

    A MagicMock console records the markup string that was passed in, which is not what
    the user sees — Rich still has to parse it. Every bracket bug in this repo survives a
    mock and only appears once something really renders, so assertions about output must
    go through a real Console.
    """
    buf = io.StringIO()
    console = Console(file=buf, width=100, force_terminal=False, no_color=True)
    with patch(f"{module_path}.console", console):
        yield buf


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
        """The panel is a thin delegator — assert the delegation, not just 'no raise'.

        This used to open `with patch(...): pass` twice and assert nothing, so the name
        was the only thing claiming delegation happened.
        """
        metrics = [Metric(label="cpu", value="50%", bar_fill=0.5)]
        with patch("navig.ui.bars.render_metric_bars") as mock_bars:
            from navig.ui.panels import render_metrics_panel
            render_metrics_panel(metrics, title="Signals")

        mock_bars.assert_called_once()
        args, kwargs = mock_bars.call_args
        assert args[0] == metrics, "the metrics must be passed straight through"
        assert kwargs["title"] == "Signals", "the title must be forwarded, not dropped"

    def test_no_raise_on_exception(self):
        # The failure must come from the code under test, not from a patch of the
        # function itself — the previous version patched `render_metrics_panel` and
        # discarded it in a `pass` block, admitting in a comment that it did nothing.
        with patch("navig.ui.bars.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.panels import render_metrics_panel
            render_metrics_panel([Metric(label="cpu", value="1", bar_fill=0.1)])


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
        """The command a user must type has to survive rendering.

        This used to redirect the console through `print()` and assert nothing — the
        name claimed the command was in the output and nothing checked it.
        """
        with _rendered("navig.ui.summary") as out:
            from navig.ui.summary import render_next_step
            render_next_step("navig db list", label="Run this")

        rendered = out.getvalue()
        assert "navig db list" in rendered, rendered
        assert "Run this" in rendered, rendered

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


# ---------------------------------------------------------------------------
# Bracketed data must survive Rich — the shapes that shipped three times before
# ---------------------------------------------------------------------------

class TestBracketedDataSurvivesRendering:
    """Rich parses `[...]` as markup, so DATA interpolated into a markup string is
    mis-rendered unless escaped. Measured on this module before the fix:

      * a label `net [eth0]` rendered as `net` — the interface silently gone;
      * a value `[/dev/sda1]` raised MarkupError (it reads as an orphan CLOSING tag),
        so the whole panel collapsed into the bare-`print` fallback, losing the table.

    Neither was visible to the existing tests because they assert against a MagicMock
    console, which records the markup string without ever parsing it. These render for
    real, which is the only way this class is observable.
    """

    def test_a_bracketed_label_is_not_swallowed(self):
        with _rendered("navig.ui.bars") as out:
            from navig.ui.bars import render_metric_bars
            render_metric_bars([Metric(label="net [eth0]", value="1Gb", bar_fill=0.9)])

        rendered = out.getvalue()
        assert "[eth0]" in rendered, f"the interface name was eaten as markup: {rendered!r}"

    def test_a_value_that_looks_like_a_closing_tag_still_renders(self):
        with _rendered("navig.ui.bars") as out:
            from navig.ui.bars import render_metric_bars
            render_metric_bars([Metric(label="mnt", value="[/dev/sda1]", bar_fill=0.2)])

        rendered = out.getvalue()
        # Unescaped this raises MarkupError, the panel is abandoned mid-render and the
        # bar never appears — so assert the BAR too, not just the text.
        assert "[/dev/sda1]" in rendered, rendered
        assert "█" in rendered or "#" in rendered, f"the panel never rendered: {rendered!r}"

    def test_escaping_does_not_disturb_column_alignment(self):
        """Anti-vacuity: escaping is only correct if the columns still line up.

        `escape()` inserts a backslash that Rich renders as nothing, so escaping BEFORE
        padding would over-pad and bend the bar column. Two labels of different literal
        length must still start their bars at the same offset.
        """
        with _rendered("navig.ui.bars") as out:
            from navig.ui.bars import render_metric_bars
            render_metric_bars([
                Metric(label="cpu", value="50%", bar_fill=0.5),
                Metric(label="net [eth0]", value="1Gb", bar_fill=0.5),
            ])

        rows = [ln for ln in out.getvalue().splitlines() if "█" in ln]
        assert len(rows) == 2, out.getvalue()
        offsets = {ln.index("█") for ln in rows}
        assert len(offsets) == 1, f"bars are misaligned across rows: {offsets} in {rows!r}"

    def test_model_output_keeps_its_brackets(self):
        """`render_ai_response` carries LLM text — the data most likely to hold `[...]`."""
        with _rendered("navig.ui.summary") as out:
            from navig.ui.summary import render_ai_response
            render_ai_response("check [1] and the path [/tmp/x] before retrying")

        rendered = out.getvalue()
        assert "[1]" in rendered, rendered
        assert "[/tmp/x]" in rendered, rendered

    def test_a_bracketed_cause_description_survives(self):
        # `[tcp]` deliberately, NOT `[8080]`: a numeric tag renders literally even
        # unescaped, so a digits-only case passes with the bug present and has no teeth.
        # Whether the bracket survives depends on the VALUE — which is exactly why this
        # class works in testing and disappears on real data.
        with _rendered("navig.ui.panels") as out:
            from navig.ui.panels import render_explanation
            render_explanation([CauseScore(confidence=80, description="socket [tcp] refused")])

        assert "[tcp]" in out.getvalue(), out.getvalue()
