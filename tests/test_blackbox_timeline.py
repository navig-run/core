"""Tests for navig.blackbox.timeline — render and format functions."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from navig.blackbox.timeline import format_event_summary, render_timeline
from navig.blackbox.types import BlackboxEvent, EventType


def _event(
    event_type: EventType,
    payload: dict,
    source: str = "navig",
) -> BlackboxEvent:
    return BlackboxEvent.create(event_type=event_type, payload=payload, source=source)


class TestFormatEventSummary:
    def test_command_with_args(self):
        e = _event(EventType.COMMAND, {"command": "host", "args": "show"})
        assert format_event_summary(e) == "host show"

    def test_command_without_args(self):
        e = _event(EventType.COMMAND, {"command": "status"})
        assert "status" in format_event_summary(e)

    def test_command_empty_payload(self):
        e = _event(EventType.COMMAND, {})
        assert format_event_summary(e) == "(unknown command)"

    def test_crash_with_exception(self):
        e = _event(EventType.CRASH, {"exception_type": "ValueError", "exception_msg": "bad"})
        result = format_event_summary(e)
        assert "ValueError" in result
        assert "bad" in result

    def test_crash_no_msg(self):
        e = _event(EventType.CRASH, {"exception_type": "RuntimeError"})
        assert format_event_summary(e) == "RuntimeError"

    def test_error_message(self):
        e = _event(EventType.ERROR, {"message": "disk full"})
        assert "disk full" in format_event_summary(e)

    def test_warning_message(self):
        e = _event(EventType.WARNING, {"message": "low battery"})
        assert "low battery" in format_event_summary(e)

    def test_session_action(self):
        e = _event(EventType.SESSION, {"action": "end"})
        assert "end" in format_event_summary(e)

    def test_session_default_action(self):
        e = _event(EventType.SESSION, {})
        assert "start" in format_event_summary(e)

    def test_output_stdout(self):
        e = _event(EventType.OUTPUT, {"stdout": "hello\nworld"})
        result = format_event_summary(e)
        assert result == "hello"

    def test_output_empty(self):
        e = _event(EventType.OUTPUT, {})
        assert format_event_summary(e) == ""

    def test_system_message(self):
        e = _event(EventType.SYSTEM, {"message": "boot"})
        assert "boot" in format_event_summary(e)


class TestRenderTimeline:
    def _mock_console(self):
        return MagicMock()

    def test_empty_list_prints_no_events_message(self):
        con = self._mock_console()
        render_timeline([], console=con)
        con.print.assert_called_once()
        call_args = str(con.print.call_args)
        assert "No blackbox" in call_args

    def test_non_empty_prints_table(self):
        events = [_event(EventType.COMMAND, {"command": "run"})]
        con = self._mock_console()
        render_timeline(events, console=con)
        con.print.assert_called_once()

    def test_limit_respected(self):
        events = [_event(EventType.SYSTEM, {"message": str(i)}) for i in range(20)]
        con = self._mock_console()
        render_timeline(events, limit=5, console=con)
        con.print.assert_called_once()

    def test_does_not_raise_on_missing_console(self):
        events = [_event(EventType.COMMAND, {"command": "test"})]
        import navig.blackbox.timeline as tl_mod
        mock_con = self._mock_console()
        with patch.object(tl_mod, "get_console", return_value=mock_con):
            render_timeline(events)  # no console arg
        mock_con.print.assert_called_once()

    def test_all_event_types_rendered(self):
        events = [
            _event(t, {"message": "x"})
            for t in EventType
        ]
        con = self._mock_console()
        render_timeline(events, console=con)
        con.print.assert_called_once()
