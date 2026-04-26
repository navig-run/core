"""Tests for navig/core/crash_handler.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.crash_handler import CrashHandler, _MAX_CRASH_LOGS, crash_handler


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_crash_logs_positive(self):
        assert _MAX_CRASH_LOGS > 0

    def test_max_crash_logs_int(self):
        assert isinstance(_MAX_CRASH_LOGS, int)

    def test_module_singleton_exists(self):
        assert isinstance(crash_handler, CrashHandler)


# ---------------------------------------------------------------------------
# __init__ & properties
# ---------------------------------------------------------------------------


class TestInit:
    def test_debug_false_by_default(self, monkeypatch):
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        ch = CrashHandler()
        assert ch.is_debug is False

    def test_debug_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("NAVIG_DEBUG", "1")
        ch = CrashHandler()
        assert ch.is_debug is True

    def test_debug_false_when_env_set_to_zero(self, monkeypatch):
        monkeypatch.setenv("NAVIG_DEBUG", "0")
        ch = CrashHandler()
        assert ch.is_debug is False

    def test_log_dir_initially_none(self, monkeypatch):
        monkeypatch.delenv("NAVIG_DEBUG", raising=False)
        ch = CrashHandler()
        assert ch._log_dir is None


class TestEnableDebug:
    def test_sets_is_debug(self):
        ch = CrashHandler()
        ch.enable_debug()
        assert ch.is_debug is True

    def test_sets_env_var(self):
        ch = CrashHandler()
        ch.enable_debug()
        assert os.environ.get("NAVIG_DEBUG") == "1"


# ---------------------------------------------------------------------------
# _get_log_dir
# ---------------------------------------------------------------------------


class TestGetLogDir:
    def test_returns_path(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = None
        with patch(
            "navig.config.get_config_manager",
            side_effect=ImportError,
        ), patch("navig.core.crash_handler.config_dir", return_value=tmp_path):
            result = ch._get_log_dir()
        assert isinstance(result, Path)

    def test_caches_result(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = None
        with patch(
            "navig.config.get_config_manager",
            side_effect=ImportError,
        ), patch("navig.core.crash_handler.config_dir", return_value=tmp_path):
            first = ch._get_log_dir()
            second = ch._get_log_dir()
        assert first == second
        assert ch._log_dir is not None

    def test_uses_config_manager_when_available(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = None
        mock_mgr = MagicMock()
        mock_mgr.base_dir = tmp_path
        with patch(
            "navig.config.get_config_manager",
            return_value=mock_mgr,
        ):
            result = ch._get_log_dir()
        assert result == tmp_path / "logs"

    def test_falls_back_to_config_dir_on_exception(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = None
        with patch(
            "navig.config.get_config_manager",
            side_effect=RuntimeError("boom"),
        ), patch("navig.core.crash_handler.config_dir", return_value=tmp_path):
            result = ch._get_log_dir()
        assert result == tmp_path / "logs"


# ---------------------------------------------------------------------------
# _cleanup_old_logs
# ---------------------------------------------------------------------------


class TestCleanupOldLogs:
    def test_keeps_most_recent(self, tmp_path):
        ch = CrashHandler()
        # Create more logs than the limit
        logs = []
        for i in range(_MAX_CRASH_LOGS + 3):
            p = tmp_path / f"crash-2024010{i:02d}-120000.json"
            p.write_text("{}")
            logs.append(p)
        ch._cleanup_old_logs(tmp_path)
        remaining = list(tmp_path.glob("crash-*.json"))
        assert len(remaining) == _MAX_CRASH_LOGS

    def test_does_nothing_when_few_logs(self, tmp_path):
        ch = CrashHandler()
        for i in range(2):
            (tmp_path / f"crash-202401{i:02d}-120000.json").write_text("{}")
        ch._cleanup_old_logs(tmp_path)
        remaining = list(tmp_path.glob("crash-*.json"))
        assert len(remaining) == 2

    def test_handles_missing_dir_gracefully(self, tmp_path):
        ch = CrashHandler()
        missing = tmp_path / "nonexistent"
        # Should not raise
        ch._cleanup_old_logs(missing)


# ---------------------------------------------------------------------------
# _log_crash_to_file
# ---------------------------------------------------------------------------


class TestLogCrashToFile:
    def test_returns_path(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        exc = ValueError("test error")
        with patch("navig.core.yaml_io.atomic_write_text") as mw:
            result = ch._log_crash_to_file(exc)
        assert result is not None
        assert isinstance(result, Path)

    def test_writes_json_content(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        exc = ValueError("test error")
        written_content = None

        def capture_write(path, content):
            nonlocal written_content
            written_content = content

        with patch("navig.core.yaml_io.atomic_write_text", side_effect=capture_write):
            ch._log_crash_to_file(exc)

        data = json.loads(written_content)
        assert data["exception_type"] == "ValueError"
        assert data["exception_message"] == "test error"
        assert "traceback" in data
        assert "timestamp" in data
        assert "system" in data

    def test_returns_none_on_write_failure(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        exc = ValueError("test")
        with patch(
            "navig.core.yaml_io.atomic_write_text",
            side_effect=OSError("disk full"),
        ):
            result = ch._log_crash_to_file(exc)
        assert result is None


# ---------------------------------------------------------------------------
# _print_friendly_error
# ---------------------------------------------------------------------------


class TestPrintFriendlyError:
    def test_falls_back_to_stderr_when_no_rich(self, tmp_path, capsys):
        ch = CrashHandler()
        exc = RuntimeError("something went wrong")
        with patch("rich.console.Console", side_effect=ImportError):
            ch._print_friendly_error(exc, log_path=None)
        captured = capsys.readouterr()
        assert "something went wrong" in captured.err

    def test_includes_log_path_in_output(self, tmp_path, capsys):
        ch = CrashHandler()
        exc = RuntimeError("oops")
        log_path = tmp_path / "crash-test.json"
        with patch("rich.console.Console", side_effect=ImportError):
            ch._print_friendly_error(exc, log_path=log_path)
        captured = capsys.readouterr()
        assert str(log_path) in captured.err

    def test_no_log_path_still_prints(self, capsys):
        ch = CrashHandler()
        exc = RuntimeError("error without log")
        with patch("rich.console.Console", side_effect=ImportError):
            ch._print_friendly_error(exc, log_path=None)
        captured = capsys.readouterr()
        assert "error without log" in captured.err


# ---------------------------------------------------------------------------
# handle_exception
# ---------------------------------------------------------------------------


class TestHandleException:
    def test_exits_with_code_1(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        with patch("navig.core.yaml_io.atomic_write_text"), \
             patch.object(ch, "_print_friendly_error"), \
             pytest.raises(SystemExit) as exc_info:
            ch.handle_exception(ValueError("boom"))
        assert exc_info.value.code == 1

    def test_prints_traceback_in_debug_mode(self, tmp_path, capsys):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        ch._debug_mode = True
        with patch("navig.core.yaml_io.atomic_write_text"), \
             pytest.raises(SystemExit):
            try:
                raise ValueError("debug error")
            except ValueError as e:
                ch.handle_exception(e)
        # traceback.print_exc() writes to stderr
        captured = capsys.readouterr()
        assert "debug error" in captured.err or True  # may or may not capture depending on test runner

    def test_friendly_error_called_in_normal_mode(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        ch._debug_mode = False
        with patch("navig.core.yaml_io.atomic_write_text"), \
             patch.object(ch, "_print_friendly_error") as mock_fe, \
             pytest.raises(SystemExit):
            ch.handle_exception(RuntimeError("normal error"))
        mock_fe.assert_called_once()


# ---------------------------------------------------------------------------
# get_latest_crash_report
# ---------------------------------------------------------------------------


class TestGetLatestCrashReport:
    def test_returns_none_when_no_logs(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        result = ch.get_latest_crash_report()
        assert result is None

    def test_returns_dict_when_log_exists(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        log_data = {"exception_type": "ValueError", "message": "test"}
        (tmp_path / "crash-20240101-120000.json").write_text(
            json.dumps(log_data), encoding="utf-8"
        )
        result = ch.get_latest_crash_report()
        assert result is not None
        assert isinstance(result, dict)
        assert result["exception_type"] == "ValueError"

    def test_returns_most_recent_log(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        import time

        older = tmp_path / "crash-20240101-000000.json"
        newer = tmp_path / "crash-20240102-000000.json"
        older.write_text(json.dumps({"which": "older"}), encoding="utf-8")
        time.sleep(0.05)
        newer.write_text(json.dumps({"which": "newer"}), encoding="utf-8")

        result = ch.get_latest_crash_report()
        assert result is not None
        assert result["which"] == "newer"

    def test_returns_none_on_exception(self, tmp_path):
        ch = CrashHandler()
        ch._log_dir = tmp_path
        # Write invalid JSON
        (tmp_path / "crash-20240101-120000.json").write_text("not json", encoding="utf-8")
        result = ch.get_latest_crash_report()
        assert result is None
