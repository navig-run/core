"""
Tests for navig.modules.context_generator
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.modules.context_generator import ContextGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator(tmp_path: Path) -> ContextGenerator:
    assistant = MagicMock()
    assistant.ai_context_dir = tmp_path
    assistant.assistant_config = {}
    return ContextGenerator(assistant)


# ---------------------------------------------------------------------------
# _get_navig_version
# ---------------------------------------------------------------------------


class TestGetNavigVersion:
    def test_returns_string(self, tmp_path):
        gen = _make_generator(tmp_path)
        v = gen._get_navig_version()
        assert isinstance(v, str)

    def test_import_error_returns_unknown(self, tmp_path):
        gen = _make_generator(tmp_path)
        with patch.dict("sys.modules", {"navig": None}):
            # falls back gracefully
            v = gen._get_navig_version()
        assert isinstance(v, str)


# ---------------------------------------------------------------------------
# _build_server_context
# ---------------------------------------------------------------------------


class TestBuildServerContext:
    def test_basic_fields_present(self, tmp_path):
        gen = _make_generator(tmp_path)
        cfg = {"name": "prod", "host": "1.2.3.4", "user": "root", "environment": "production"}
        ctx = gen._build_server_context(cfg)
        assert ctx["name"] == "prod"
        assert ctx["host"] == "1.2.3.4"
        assert ctx["user"] == "root"
        assert ctx["environment"] == "production"

    def test_with_remote_ops_adds_os(self, tmp_path):
        gen = _make_generator(tmp_path)
        cfg = {"name": "s", "host": "h", "user": "u", "environment": "dev"}

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Ubuntu 22.04"

        remote_ops = MagicMock()
        remote_ops.execute_command.return_value = mock_result

        ctx = gen._build_server_context(cfg, remote_ops=remote_ops)
        assert "os" in ctx
        assert "Ubuntu" in ctx["os"]

    def test_with_remote_ops_exception_records_error(self, tmp_path):
        gen = _make_generator(tmp_path)
        cfg = {"name": "s", "host": "h", "user": "u", "environment": "dev"}

        remote_ops = MagicMock()
        remote_ops.execute_command.side_effect = RuntimeError("SSH failure")

        ctx = gen._build_server_context(cfg, remote_ops=remote_ops)
        assert "live_data_error" in ctx


# ---------------------------------------------------------------------------
# _get_recent_operations
# ---------------------------------------------------------------------------


class TestGetRecentOperations:
    def test_no_history_file_returns_empty(self, tmp_path):
        gen = _make_generator(tmp_path)
        ops = gen._get_recent_operations()
        assert ops == []

    def test_reads_history(self, tmp_path):
        gen = _make_generator(tmp_path)
        hist = [{"cmd": "ls"}, {"cmd": "pwd"}]
        (tmp_path / "command_history.json").write_text(
            json.dumps(hist), encoding="utf-8"
        )
        ops = gen._get_recent_operations()
        assert len(ops) == 2

    def test_limit_respected(self, tmp_path):
        gen = _make_generator(tmp_path)
        hist = [{"cmd": f"cmd{i}"} for i in range(50)]
        (tmp_path / "command_history.json").write_text(
            json.dumps(hist), encoding="utf-8"
        )
        ops = gen._get_recent_operations(limit=10)
        assert len(ops) == 10
        # Should be the LAST 10 items
        assert ops[-1]["cmd"] == "cmd49"

    def test_corrupt_file_returns_empty(self, tmp_path):
        gen = _make_generator(tmp_path)
        (tmp_path / "command_history.json").write_text("{bad}", encoding="utf-8")
        ops = gen._get_recent_operations()
        assert ops == []


# ---------------------------------------------------------------------------
# _get_active_issues
# ---------------------------------------------------------------------------


class TestGetActiveIssues:
    def test_no_file_returns_empty(self, tmp_path):
        gen = _make_generator(tmp_path)
        assert gen._get_active_issues() == []

    def test_active_issue_within_24h(self, tmp_path):
        gen = _make_generator(tmp_path)
        issues = [
            {
                "status": "active",
                "timestamp": (datetime.now() - timedelta(hours=1)).isoformat(),
                "msg": "disk full",
            }
        ]
        (tmp_path / "detected_issues.json").write_text(
            json.dumps(issues), encoding="utf-8"
        )
        result = gen._get_active_issues()
        assert len(result) == 1

    def test_stale_active_issue_excluded(self, tmp_path):
        gen = _make_generator(tmp_path)
        issues = [
            {
                "status": "active",
                "timestamp": (datetime.now() - timedelta(hours=30)).isoformat(),
            }
        ]
        (tmp_path / "detected_issues.json").write_text(
            json.dumps(issues), encoding="utf-8"
        )
        result = gen._get_active_issues()
        assert result == []

    def test_resolved_issue_excluded(self, tmp_path):
        gen = _make_generator(tmp_path)
        issues = [
            {
                "status": "resolved",
                "timestamp": datetime.now().isoformat(),
            }
        ]
        (tmp_path / "detected_issues.json").write_text(
            json.dumps(issues), encoding="utf-8"
        )
        assert gen._get_active_issues() == []

    def test_corrupt_file_returns_empty(self, tmp_path):
        gen = _make_generator(tmp_path)
        (tmp_path / "detected_issues.json").write_text("{bad}", encoding="utf-8")
        assert gen._get_active_issues() == []


# ---------------------------------------------------------------------------
# _get_recent_errors
# ---------------------------------------------------------------------------


class TestGetRecentErrors:
    def test_returns_empty_when_no_error_resolution(self, tmp_path):
        gen = _make_generator(tmp_path)
        gen.assistant = MagicMock(spec=[])  # no error_resolution attr
        result = gen._get_recent_errors()
        assert result == []

    def test_delegates_to_error_resolution(self, tmp_path):
        gen = _make_generator(tmp_path)
        gen.assistant.error_resolution = MagicMock()
        gen.assistant.error_resolution.get_error_statistics.return_value = {
            "recent_errors": [{"msg": "oops"}]
        }
        result = gen._get_recent_errors(hours=12)
        assert result == [{"msg": "oops"}]
        gen.assistant.error_resolution.get_error_statistics.assert_called_once_with(hours=12)


# ---------------------------------------------------------------------------
# _generate_summary
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    def test_empty_context(self, tmp_path):
        gen = _make_generator(tmp_path)
        summary = gen._generate_summary({})
        assert summary == "No context available"

    def test_with_server(self, tmp_path):
        gen = _make_generator(tmp_path)
        ctx = {"server": {"name": "prod", "host": "1.2.3.4"}}
        summary = gen._generate_summary(ctx)
        assert "prod" in summary
        assert "1.2.3.4" in summary

    def test_with_running_services(self, tmp_path):
        gen = _make_generator(tmp_path)
        ctx = {
            "services": [
                {"name": "nginx", "status": "running"},
                {"name": "mysql", "status": "stopped"},
            ]
        }
        summary = gen._generate_summary(ctx)
        assert "nginx" in summary
        assert "mysql" not in summary  # not running

    def test_with_active_issues(self, tmp_path):
        gen = _make_generator(tmp_path)
        ctx = {"active_issues": [{"msg": "disk full"}, {"msg": "cpu high"}]}
        summary = gen._generate_summary(ctx)
        assert "2" in summary

    def test_with_recent_operations(self, tmp_path):
        gen = _make_generator(tmp_path)
        ctx = {"recent_operations": [{"cmd": "ls"}, {"cmd": "pwd"}]}
        summary = gen._generate_summary(ctx)
        assert "2" in summary


# ---------------------------------------------------------------------------
# generate_context_summary (integration-ish, mocked)
# ---------------------------------------------------------------------------


class TestGenerateContextSummary:
    def test_returns_dict_with_expected_keys(self, tmp_path):
        gen = _make_generator(tmp_path)
        mock_cm = MagicMock()
        mock_cm.get_active_server.return_value = None  # no active server

        result = gen.generate_context_summary(mock_cm)

        assert "generated_at" in result
        assert "navig_version" in result
        assert "client_platform" in result
        assert "recent_operations" in result
        assert "context_summary" in result

    def test_server_exception_recorded(self, tmp_path):
        gen = _make_generator(tmp_path)
        mock_cm = MagicMock()
        mock_cm.get_active_server.return_value = "prod"
        mock_cm.load_server_config.side_effect = RuntimeError("config not found")

        result = gen.generate_context_summary(mock_cm)
        assert "error" in result["server"]
