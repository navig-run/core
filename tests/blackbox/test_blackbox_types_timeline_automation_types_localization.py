"""
Batch 49 — hermetic unit tests for:
  navig/blackbox/types.py                  — EventType, BlackboxEvent, Bundle
  navig/blackbox/timeline.py               — format_event_summary, render_timeline
  navig/adapters/automation/types.py       — ExecutionResult, WindowInfo
  navig/agent/conv/localization.py         — LocalizationStore
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/blackbox/types.py — EventType, BlackboxEvent, Bundle
# ---------------------------------------------------------------------------

from navig.blackbox.types import BlackboxEvent, Bundle, EventType, _SEVERITY


class TestEventType:
    def test_all_values_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)

    def test_command_value(self):
        assert EventType.COMMAND.value == "command"

    def test_crash_value(self):
        assert EventType.CRASH.value == "crash"

    def test_error_value(self):
        assert EventType.ERROR.value == "error"

    def test_warning_value(self):
        assert EventType.WARNING.value == "warning"

    def test_session_value(self):
        assert EventType.SESSION.value == "session"

    def test_system_value(self):
        assert EventType.SYSTEM.value == "system"

    def test_output_value(self):
        assert EventType.OUTPUT.value == "output"

    def test_severity_crash_highest(self):
        assert _SEVERITY[EventType.CRASH] > _SEVERITY[EventType.ERROR]

    def test_severity_error_above_warning(self):
        assert _SEVERITY[EventType.ERROR] > _SEVERITY[EventType.WARNING]

    def test_severity_system_lowest(self):
        assert _SEVERITY[EventType.SYSTEM] == 0


class TestBlackboxEvent:
    def _sample(self, etype=EventType.COMMAND, payload=None):
        return BlackboxEvent.create(
            event_type=etype,
            payload=payload or {"command": "navig", "args": "host list"},
        )

    def test_create_returns_event(self):
        ev = self._sample()
        assert isinstance(ev, BlackboxEvent)

    def test_create_sets_event_type(self):
        ev = self._sample(EventType.ERROR, {"message": "oops"})
        assert ev.event_type == EventType.ERROR

    def test_create_id_nonempty(self):
        ev = self._sample()
        assert ev.id

    def test_create_source_default(self):
        ev = self._sample()
        assert ev.source == "navig"

    def test_create_tags_default_empty(self):
        ev = self._sample()
        assert ev.tags == []

    def test_create_with_tags(self):
        ev = BlackboxEvent.create(EventType.SESSION, {}, tags=["a", "b"])
        assert "a" in ev.tags

    def test_create_with_custom_source(self):
        ev = BlackboxEvent.create(EventType.SYSTEM, {}, source="daemon")
        assert ev.source == "daemon"

    def test_to_json_is_valid_json(self):
        ev = self._sample()
        parsed = json.loads(ev.to_json())
        assert parsed["event_type"] == "command"

    def test_to_json_contains_id(self):
        ev = self._sample()
        parsed = json.loads(ev.to_json())
        assert "id" in parsed

    def test_to_json_timestamp_is_iso(self):
        ev = self._sample()
        parsed = json.loads(ev.to_json())
        ts = parsed["timestamp"]
        assert "T" in ts or " " in ts

    def test_from_dict_round_trip(self):
        ev = self._sample()
        d = json.loads(ev.to_json())
        restored = BlackboxEvent.from_dict(d)
        assert restored.id == ev.id
        assert restored.event_type == ev.event_type
        assert restored.source == ev.source

    def test_severity_method(self):
        ev = self._sample(EventType.CRASH, {"exception_type": "RuntimeError"})
        assert ev.severity() == _SEVERITY[EventType.CRASH]

    def test_severity_command_is_numeric(self):
        ev = self._sample()
        assert isinstance(ev.severity(), int)


class TestBundle:
    def _sample_bundle(self):
        return Bundle(
            id="bundle-001",
            created_at=datetime.now(timezone.utc),
            navig_version="2.0.0",
            events=[
                BlackboxEvent.create(EventType.COMMAND, {"command": "ls"}),
                BlackboxEvent.create(EventType.ERROR, {"message": "err"}),
            ],
            crash_reports=[{"exception": "RuntimeError"}],
            log_tails={"app.log": "last line"},
            manifest_hash="abc123",
        )

    def test_event_count(self):
        b = self._sample_bundle()
        assert b.event_count() == 2

    def test_crash_count(self):
        b = self._sample_bundle()
        assert b.crash_count() == 1

    def test_compute_hash_returns_hex(self):
        h = Bundle.compute_hash(b"hello world")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_deterministic(self):
        assert Bundle.compute_hash(b"data") == Bundle.compute_hash(b"data")

    def test_sealed_default_false(self):
        b = self._sample_bundle()
        assert b.sealed is False


# ---------------------------------------------------------------------------
# navig/blackbox/timeline.py — format_event_summary
# ---------------------------------------------------------------------------

from navig.blackbox.timeline import format_event_summary, render_timeline


def _ev(etype: EventType, payload: dict) -> BlackboxEvent:
    return BlackboxEvent.create(event_type=etype, payload=payload)


class TestFormatEventSummary:
    def test_command_event(self):
        ev = _ev(EventType.COMMAND, {"command": "navig", "args": "db list"})
        result = format_event_summary(ev)
        assert "navig" in result
        assert "db list" in result

    def test_command_no_args(self):
        ev = _ev(EventType.COMMAND, {"command": "navig"})
        result = format_event_summary(ev)
        assert "navig" in result

    def test_crash_event(self):
        ev = _ev(EventType.CRASH, {"exception_type": "ValueError", "exception_msg": "bad input"})
        result = format_event_summary(ev)
        assert "ValueError" in result
        assert "bad input" in result

    def test_crash_no_message(self):
        ev = _ev(EventType.CRASH, {"exception_type": "TypeError"})
        result = format_event_summary(ev)
        assert "TypeError" in result

    def test_error_event_message(self):
        ev = _ev(EventType.ERROR, {"message": "something went wrong"})
        result = format_event_summary(ev)
        assert "something went wrong" in result

    def test_warning_event(self):
        ev = _ev(EventType.WARNING, {"message": "deprecated flag"})
        result = format_event_summary(ev)
        assert "deprecated flag" in result

    def test_session_start(self):
        ev = _ev(EventType.SESSION, {"action": "start"})
        result = format_event_summary(ev)
        assert "start" in result.lower()

    def test_session_end(self):
        ev = _ev(EventType.SESSION, {"action": "end"})
        result = format_event_summary(ev)
        assert "end" in result.lower()

    def test_output_event(self):
        ev = _ev(EventType.OUTPUT, {"stdout": "line1\nline2"})
        result = format_event_summary(ev)
        assert "line1" in result

    def test_system_event(self):
        ev = _ev(EventType.SYSTEM, {"message": "daemon started"})
        result = format_event_summary(ev)
        assert "daemon started" in result

    def test_result_is_at_most_120_chars(self):
        long_msg = "x" * 200
        ev = _ev(EventType.ERROR, {"message": long_msg})
        result = format_event_summary(ev)
        assert len(result) <= 120


class TestRenderTimeline:
    def test_empty_events_prints_no_events_message(self):
        mock_console = MagicMock()
        render_timeline([], console=mock_console)
        mock_console.print.assert_called_once()
        call_arg = mock_console.print.call_args[0][0]
        assert "No blackbox" in call_arg

    def test_events_renders_table(self):
        from rich.table import Table

        mock_console = MagicMock()
        events = [_ev(EventType.COMMAND, {"command": "navig"})]
        render_timeline(events, console=mock_console)
        mock_console.print.assert_called_once()
        arg = mock_console.print.call_args[0][0]
        assert isinstance(arg, Table)

    def test_limit_caps_displayed_rows(self):
        events = [_ev(EventType.COMMAND, {"command": f"cmd{i}"}) for i in range(10)]
        mock_console = MagicMock()
        render_timeline(events, limit=3, console=mock_console)
        mock_console.print.assert_called_once()


# ---------------------------------------------------------------------------
# navig/adapters/automation/types.py — ExecutionResult, WindowInfo
# ---------------------------------------------------------------------------

from navig.adapters.automation.types import ExecutionResult, WindowInfo


class TestExecutionResult:
    def test_success_default(self):
        r = ExecutionResult(success=True)
        assert r.success is True

    def test_stdout_default_empty(self):
        r = ExecutionResult(success=True)
        assert r.stdout == ""

    def test_stderr_default_empty(self):
        r = ExecutionResult(success=False)
        assert r.stderr == ""

    def test_exit_code_default_zero(self):
        r = ExecutionResult(success=True)
        assert r.exit_code == 0

    def test_duration_default_zero(self):
        r = ExecutionResult(success=True)
        assert r.duration_seconds == 0.0

    def test_status_default_completed(self):
        r = ExecutionResult(success=True)
        assert r.status == "COMPLETED"

    def test_custom_values(self):
        r = ExecutionResult(
            success=False,
            stdout="output",
            stderr="error",
            exit_code=1,
            duration_seconds=2.5,
            status="FAILED",
        )
        assert r.exit_code == 1
        assert r.status == "FAILED"
        assert r.duration_seconds == 2.5


class TestWindowInfo:
    def _make(self, **kw):
        defaults = dict(title="My Window", id="12345", pid=100,
                        class_name="AppClass", x=0, y=0, width=800, height=600)
        defaults.update(kw)
        return WindowInfo(**defaults)

    def test_to_dict_contains_title(self):
        w = self._make(title="Notepad")
        d = w.to_dict()
        assert d["title"] == "Notepad"

    def test_to_dict_state_normal(self):
        w = self._make()
        assert w.to_dict()["state"] == "normal"

    def test_to_dict_state_minimized(self):
        w = self._make(is_minimized=True)
        assert w.to_dict()["state"] == "minimized"

    def test_to_dict_state_maximized(self):
        w = self._make(is_maximized=True)
        assert w.to_dict()["state"] == "maximized"

    def test_to_dict_contains_geometry(self):
        w = self._make(x=10, y=20, width=1024, height=768)
        d = w.to_dict()
        assert d["x"] == 10 and d["y"] == 20
        assert d["width"] == 1024 and d["height"] == 768

    def test_process_name_default_none(self):
        w = self._make()
        assert w.process_name is None

    def test_to_dict_process_name_none(self):
        w = self._make()
        assert w.to_dict()["process_name"] is None


# ---------------------------------------------------------------------------
# navig/agent/conv/localization.py — LocalizationStore
# ---------------------------------------------------------------------------

from navig.agent.conv.localization import LocalizationStore


class TestLocalizationStore:
    def test_get_returns_key_when_no_files(self, tmp_path):
        store = LocalizationStore(locales_root=tmp_path)
        result = store.get("hello", lang="es")
        assert result == "hello"

    def test_get_value_from_locale_file(self, tmp_path):
        locale_file = tmp_path / "en.json"
        locale_file.write_text(json.dumps({"greeting": "Hello"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        assert store.get("greeting", lang="en") == "Hello"

    def test_fallback_to_english(self, tmp_path):
        en = tmp_path / "en.json"
        en.write_text(json.dumps({"cancel": "Cancel"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        # French not available — falls back to English
        result = store.get("cancel", lang="fr")
        assert result == "Cancel"

    def test_missing_key_returns_key_itself(self, tmp_path):
        en = tmp_path / "en.json"
        en.write_text(json.dumps({"known": "Known"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        result = store.get("unknown_key", lang="en")
        assert result == "unknown_key"

    def test_cache_reuse(self, tmp_path):
        locale = tmp_path / "en.json"
        locale.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        store.get("k", lang="en")
        # Second access should use cache — still returns correct value
        assert store.get("k", lang="en") == "v"

    def test_preload_warms_cache(self, tmp_path):
        locale = tmp_path / "de.json"
        locale.write_text(json.dumps({"yes": "Ja"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        store.preload("de")
        assert "de" in store._cache

    def test_invalid_json_does_not_raise(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("NOT JSON", encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        result = store.get("key", lang="bad")
        assert result == "key"

    def test_empty_json_object_returns_key(self, tmp_path):
        empty = tmp_path / "en.json"
        empty.write_text("{}", encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        assert store.get("any_key", lang="en") == "any_key"

    def test_locale_file_with_multiple_keys(self, tmp_path):
        locale = tmp_path / "en.json"
        locale.write_text(json.dumps({"a": "Alpha", "b": "Beta"}), encoding="utf-8")
        store = LocalizationStore(locales_root=tmp_path)
        assert store.get("a", lang="en") == "Alpha"
        assert store.get("b", lang="en") == "Beta"
