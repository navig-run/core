"""Tests for navig.ui.status and navig.ui.timeline (render helpers)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.ui.models import Event, StatusChip


# ---------------------------------------------------------------------------
# navig.ui.status — render_status_header
# ---------------------------------------------------------------------------

class TestRenderStatusHeader:
    def setup_method(self):
        from navig.ui import status as _mod
        self._mod = _mod

    def _call(self, chips, **kw):
        with patch.object(self._mod.console, "print") as mp:
            self._mod.render_status_header(chips, **kw)
        return mp

    def test_empty_list_no_print(self):
        mp = self._call([])
        mp.assert_not_called()

    def test_single_chip_with_value(self):
        chip = StatusChip(icon="●", icon_safe="+", label="host", value="prod", color="green")
        mp = self._call([chip])
        mp.assert_called_once()
        output = mp.call_args[0][0]
        assert "host" in output
        assert "prod" in output

    def test_single_chip_no_value(self):
        chip = StatusChip(icon="◉", icon_safe="O", label="daemon", value=None, color="cyan")
        mp = self._call([chip])
        mp.assert_called_once()
        output = mp.call_args[0][0]
        assert "daemon" in output

    def test_multiple_chips_joined_by_sep(self):
        chips = [
            StatusChip(icon="●", icon_safe="+", label="a", value="1", color="green"),
            StatusChip(icon="●", icon_safe="+", label="b", value="2", color="blue"),
        ]
        mp = self._call(chips, sep=" | ")
        output = mp.call_args[0][0]
        assert " | " in output

    def test_never_raises_on_bad_chip(self):
        """render_status_header swallows all exceptions."""
        bad_chip = MagicMock()
        bad_chip.icon = object()   # won't format nicely
        bad_chip.label = "x"
        bad_chip.value = None
        bad_chip.color = "green"
        bad_chip.icon_safe = "x"
        # Should not raise
        self._mod.render_status_header([bad_chip])

    def test_custom_separator(self):
        chips = [
            StatusChip(icon="●", icon_safe="+", label="x", value="1", color="green"),
            StatusChip(icon="●", icon_safe="+", label="y", value="2", color="green"),
        ]
        mp = self._call(chips, sep="  ///  ")
        output = mp.call_args[0][0]
        assert "///" in output


# ---------------------------------------------------------------------------
# navig.ui.timeline — render_event_timeline
# ---------------------------------------------------------------------------

class TestRenderEventTimeline:
    def setup_method(self):
        from navig.ui import timeline as _mod
        self._mod = _mod

    def _call(self, events, **kw):
        with patch.object(self._mod.console, "print") as mp:
            self._mod.render_event_timeline(events, **kw)
        return mp

    def test_empty_events_no_output(self):
        mp = self._call([])
        mp.assert_not_called()

    def test_single_event_renders(self):
        ev = Event(timestamp="12:00", icon="✓", label="boot", detail="started", color="green")
        mp = self._call([ev])
        assert mp.call_count >= 1   # title + event

    def test_show_title_false_skips_title(self):
        ev = Event(timestamp="12:00", icon="✓", label="boot", detail="ok", color="green")
        with patch.object(self._mod.console, "print") as mp:
            self._mod.render_event_timeline([ev], title="Events", show_title=False)
        # Only 1 call (the event line), not 2
        assert mp.call_count == 1

    def test_show_title_true_adds_title(self):
        ev = Event(timestamp="12:00", icon="✓", label="ev", detail="d", color="green")
        mp = self._call([ev], title="MyTitle", show_title=True)
        calls = [str(c) for c in mp.call_args_list]
        assert any("MyTitle" in c for c in calls)

    def test_never_raises_on_bad_event(self):
        bad_ev = MagicMock()
        bad_ev.timestamp = "now"
        bad_ev.icon = "x"
        bad_ev.icon_safe = "x"
        bad_ev.label = "lbl"
        bad_ev.detail = "det"
        bad_ev.color = "green"
        # Should not raise
        self._mod.render_event_timeline([bad_ev])

    def test_multiple_events(self):
        events = [
            Event(timestamp=f"{i}:00", icon="●", label=f"e{i}", detail="d", color="blue")
            for i in range(3)
        ]
        mp = self._call(events, show_title=False)
        assert mp.call_count == 3
