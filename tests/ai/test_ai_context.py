"""Hermetic unit tests for navig.ai_context."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# ErrorLog
# ---------------------------------------------------------------------------


class TestErrorLog:
    def _make(self, **kwargs):
        from navig.ai_context import ErrorLog

        defaults = {
            "timestamp": datetime(2025, 1, 1, 12, 0, 0),
            "category": "network",
            "command": "navig tunnel open",
            "error": "Connection refused",
            "context": {"host": "prod"},
        }
        defaults.update(kwargs)
        return ErrorLog(**defaults)

    def test_to_dict_contains_required_keys(self):
        entry = self._make()
        d = entry.to_dict()
        assert "timestamp" in d
        assert "category" in d
        assert "command" in d
        assert "error" in d
        assert "context" in d

    def test_to_dict_timestamp_is_isoformat(self):
        entry = self._make(timestamp=datetime(2025, 6, 15, 10, 30, 0))
        assert entry.to_dict()["timestamp"] == "2025-06-15T10:30:00"

    def test_from_dict_roundtrip(self):
        from navig.ai_context import ErrorLog

        entry = self._make()
        d = entry.to_dict()
        restored = ErrorLog.from_dict(d)
        assert restored.category == entry.category
        assert restored.command == entry.command
        assert restored.error == entry.error
        assert restored.context == entry.context

    def test_from_dict_missing_context_defaults_empty(self):
        from navig.ai_context import ErrorLog

        d = {
            "timestamp": "2025-01-01T00:00:00",
            "category": "file",
            "command": "ls",
            "error": "Not found",
        }
        restored = ErrorLog.from_dict(d)
        assert restored.context == {}


# ---------------------------------------------------------------------------
# AIContextManager — init and error logging
# ---------------------------------------------------------------------------


class TestAIContextManagerInit:
    def test_creates_config_dir(self, tmp_path):
        from navig.ai_context import AIContextManager

        d = tmp_path / "navig_ctx"
        mgr = AIContextManager(config_dir=d)
        assert d.is_dir()

    def test_starts_with_empty_errors_on_missing_log_file(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        assert mgr.error_logs == []

    def test_loads_existing_error_log_file(self, tmp_path):
        from navig.ai_context import AIContextManager, ErrorLog

        entry = {
            "timestamp": "2025-01-01T00:00:00",
            "category": "tunnel",
            "command": "open",
            "error": "err",
            "context": {},
        }
        (tmp_path / "error_log.json").write_text(json.dumps([entry]))
        mgr = AIContextManager(config_dir=tmp_path)
        assert len(mgr.error_logs) == 1
        assert mgr.error_logs[0].category == "tunnel"


class TestAIContextManagerLogError:
    def test_log_error_appends_to_list(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.log_error("network", "ping", "timeout")
        assert len(mgr.error_logs) == 1
        assert mgr.error_logs[0].error == "timeout"

    def test_log_error_persists_to_file(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.log_error("config", "validate", "bad yaml", context={"file": "test.yaml"})

        data = json.loads((tmp_path / "error_log.json").read_text())
        assert len(data) == 1
        assert data[0]["command"] == "validate"

    def test_max_error_logs_trimmed(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.MAX_ERROR_LOGS = 5
        for i in range(10):
            mgr.log_error("test", f"cmd{i}", f"err{i}")

        assert len(mgr.error_logs) <= 5


# ---------------------------------------------------------------------------
# get_recent_errors
# ---------------------------------------------------------------------------


class TestGetRecentErrors:
    def test_returns_all_recent_errors_within_window(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.log_error("network", "ping", "timeout")
        mgr.log_error("file", "ls", "not found")

        result = mgr.get_recent_errors(hours=1)
        assert len(result) == 2

    def test_filters_by_category(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.log_error("network", "ping", "timeout")
        mgr.log_error("file", "ls", "not found")

        result = mgr.get_recent_errors(hours=1, category="network")
        assert all(e.category == "network" for e in result)
        assert len(result) == 1

    def test_respects_limit(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        for i in range(10):
            mgr.log_error("test", f"cmd{i}", "err")

        result = mgr.get_recent_errors(hours=1, limit=3)
        assert len(result) <= 3

    def test_excludes_old_errors(self, tmp_path):
        from navig.ai_context import AIContextManager, ErrorLog

        mgr = AIContextManager(config_dir=tmp_path)
        # Manually add an old error
        old = ErrorLog(
            timestamp=datetime.now() - timedelta(hours=48),
            category="old",
            command="old_cmd",
            error="old error",
            context={},
        )
        mgr.error_logs.append(old)
        mgr.log_error("new", "new_cmd", "new error")

        result = mgr.get_recent_errors(hours=1)
        assert all(e.category != "old" for e in result)


# ---------------------------------------------------------------------------
# get_error_summary
# ---------------------------------------------------------------------------


class TestGetErrorSummary:
    def test_empty_errors_returns_zero_total(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        summary = mgr.get_error_summary(hours=24)
        assert summary["total_errors"] == 0

    def test_summary_counts_by_category(self, tmp_path):
        from navig.ai_context import AIContextManager

        mgr = AIContextManager(config_dir=tmp_path)
        mgr.log_error("network", "ping", "timeout")
        mgr.log_error("network", "curl", "refused")
        mgr.log_error("file", "ls", "not found")

        summary = mgr.get_error_summary(hours=1)
        assert summary["total_errors"] == 3
