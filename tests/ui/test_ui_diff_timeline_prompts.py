"""Batch 72 — ui/diff, ui/timeline, ui/prompts."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.ui.models import DiffLine, DiffPreview, Event


# ---------------------------------------------------------------------------
# navig.ui.diff — diff_lines_from_text, render_diff_preview
# ---------------------------------------------------------------------------

class TestDiffLinesFromText:
    def test_added_line(self):
        from navig.ui.diff import diff_lines_from_text
        lines = diff_lines_from_text("hello", "hello\nworld")
        ops = [l.op for l in lines]
        assert "add" in ops

    def test_removed_line(self):
        from navig.ui.diff import diff_lines_from_text
        lines = diff_lines_from_text("hello\nworld", "hello")
        ops = [l.op for l in lines]
        assert "remove" in ops

    def test_identical_no_changes(self):
        from navig.ui.diff import diff_lines_from_text
        lines = diff_lines_from_text("same\ntext", "same\ntext")
        # No add/remove lines
        change_ops = [l.op for l in lines if l.op in ("add", "remove")]
        assert len(change_ops) == 0

    def test_returns_list_of_diffline(self):
        from navig.ui.diff import diff_lines_from_text
        lines = diff_lines_from_text("a", "b")
        assert all(isinstance(l, DiffLine) for l in lines)

    def test_content_preserved(self):
        from navig.ui.diff import diff_lines_from_text
        lines = diff_lines_from_text("old line", "new line")
        contents = [l.content for l in lines]
        assert any("new line" in c or "old line" in c for c in contents)


class TestRenderDiffPreview:
    def _make_diff(self):
        return DiffPreview(
            title="Changes",
            lines=[
                DiffLine(op="add", content="new line"),
                DiffLine(op="remove", content="old line"),
                DiffLine(op="context", content="context line"),
            ],
        )

    def test_skipped_when_debug_false_no_env(self):
        with patch("navig.ui.diff.console") as mock_c:
            with patch("os.getenv", return_value="0"):
                from navig.ui.diff import render_diff_preview
                render_diff_preview(self._make_diff(), debug=False)
        mock_c.print.assert_not_called()

    def test_renders_when_debug_true(self):
        with patch("navig.ui.diff.console") as mock_c:
            from navig.ui.diff import render_diff_preview
            render_diff_preview(self._make_diff(), debug=True)
        assert mock_c.print.called

    def test_title_printed(self):
        with patch("navig.ui.diff.console") as mock_c:
            from navig.ui.diff import render_diff_preview
            render_diff_preview(self._make_diff(), debug=True)
        first_call = str(mock_c.print.call_args_list[0])
        assert "Changes" in first_call

    def test_max_lines_truncates(self):
        diff = DiffPreview(
            title="Big diff",
            lines=[DiffLine(op="context", content=f"line {i}") for i in range(10)],
        )
        with patch("navig.ui.diff.console") as mock_c:
            from navig.ui.diff import render_diff_preview
            render_diff_preview(diff, debug=True, max_lines=3)
        # title + 3 lines + "more lines" line = 5 calls
        assert mock_c.print.call_count == 5

    def test_empty_lines_no_output(self):
        diff = DiffPreview(title="Empty", lines=[])
        with patch("navig.ui.diff.console") as mock_c:
            from navig.ui.diff import render_diff_preview
            render_diff_preview(diff, debug=True)
        mock_c.print.assert_not_called()

    def test_no_raise_on_exception(self):
        with patch("navig.ui.diff.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.diff import render_diff_preview
            render_diff_preview(self._make_diff(), debug=True)


# ---------------------------------------------------------------------------
# navig.ui.timeline — render_event_timeline
# ---------------------------------------------------------------------------

def _make_event(label="Deploy", detail="succeeded"):
    return Event(
        timestamp="12:00:00",
        icon="✓",
        label=label,
        detail=detail,
        color="green",
    )


class TestRenderEventTimeline:
    def test_empty_no_output(self):
        with patch("navig.ui.timeline.console") as mock_c:
            from navig.ui.timeline import render_event_timeline
            render_event_timeline([])
        mock_c.print.assert_not_called()

    def test_renders_events(self):
        events = [_make_event(), _make_event("Restart", "done")]
        with patch("navig.ui.timeline.console") as mock_c:
            from navig.ui.timeline import render_event_timeline
            render_event_timeline(events)
        # title + 2 event rows = 3
        assert mock_c.print.call_count == 3

    def test_show_title_false_skips_title(self):
        events = [_make_event()]
        with patch("navig.ui.timeline.console") as mock_c:
            from navig.ui.timeline import render_event_timeline
            render_event_timeline(events, show_title=False)
        # Only 1 line (the event row)
        assert mock_c.print.call_count == 1

    def test_custom_title(self):
        events = [_make_event()]
        with patch("navig.ui.timeline.console") as mock_c:
            from navig.ui.timeline import render_event_timeline
            render_event_timeline(events, title="Deploy History")
        first_call = str(mock_c.print.call_args_list[0])
        assert "Deploy History" in first_call

    def test_no_raise_on_exception(self):
        events = [_make_event()]
        with patch("navig.ui.timeline.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.timeline import render_event_timeline
            render_event_timeline(events)


# ---------------------------------------------------------------------------
# navig.ui.prompts — render_keymap_footer, render_action_approval
# ---------------------------------------------------------------------------

class TestRenderKeymapFooter:
    def test_empty_keymap_no_output(self):
        with patch("navig.ui.prompts.console") as mock_c:
            from navig.ui.prompts import render_keymap_footer
            render_keymap_footer({})
        mock_c.print.assert_not_called()

    def test_renders_single_binding(self):
        with patch("navig.ui.prompts.console") as mock_c:
            from navig.ui.prompts import render_keymap_footer
            render_keymap_footer({"q": "quit"})
        mock_c.print.assert_called_once()

    def test_multiple_bindings_single_print(self):
        with patch("navig.ui.prompts.console") as mock_c:
            from navig.ui.prompts import render_keymap_footer
            render_keymap_footer({"q": "quit", "r": "refresh", "h": "help"})
        mock_c.print.assert_called_once()

    def test_no_raise_on_exception(self):
        with patch("navig.ui.prompts.console") as mock_c:
            mock_c.print.side_effect = RuntimeError("boom")
            from navig.ui.prompts import render_keymap_footer
            render_keymap_footer({"q": "quit"})


class TestRenderActionApproval:
    def test_returns_true_on_yes(self):
        with patch("navig.ui.prompts.console"):
            with patch("builtins.input", return_value="y"):
                from navig.ui.prompts import render_action_approval
                result = render_action_approval("navig run --help")
        assert result is True

    def test_returns_true_on_full_yes(self):
        with patch("navig.ui.prompts.console"):
            with patch("builtins.input", return_value="yes"):
                from navig.ui.prompts import render_action_approval
                result = render_action_approval("cmd")
        assert result is True

    def test_returns_false_on_no(self):
        with patch("navig.ui.prompts.console"):
            with patch("builtins.input", return_value="n"):
                from navig.ui.prompts import render_action_approval
                result = render_action_approval("cmd")
        assert result is False

    def test_returns_false_on_eoferror(self):
        with patch("navig.ui.prompts.console"):
            with patch("builtins.input", side_effect=EOFError):
                from navig.ui.prompts import render_action_approval
                result = render_action_approval("cmd")
        assert result is False

    def test_hint_triggers_extra_print(self):
        with patch("navig.ui.prompts.console") as mock_c:
            with patch("builtins.input", return_value="n"):
                from navig.ui.prompts import render_action_approval
                render_action_approval("cmd", hint="This is risky")
        # Should have at least 3 prints: command, hint, prompt
        assert mock_c.print.call_count >= 3
