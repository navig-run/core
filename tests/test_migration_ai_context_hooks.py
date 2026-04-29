"""Batch 109: tests for migration, ai_context, assistant_hooks."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

class TestDetectFormat:
    def test_detects_new_format_with_apps(self, tmp_path):
        from navig.migration import detect_format
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"apps": {}}), encoding="utf-8")
        assert detect_format(cfg) == "new"

    def test_detects_old_format_with_host(self, tmp_path):
        from navig.migration import detect_format
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"host": "example.com", "services": {}}), encoding="utf-8")
        assert detect_format(cfg) == "old"

    def test_raises_file_not_found(self, tmp_path):
        from navig.migration import detect_format
        with pytest.raises(FileNotFoundError):
            detect_format(tmp_path / "missing.yaml")

    def test_raises_on_empty_file(self, tmp_path):
        from navig.migration import detect_format, ConfigMigrationError
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("", encoding="utf-8")
        with pytest.raises(ConfigMigrationError):
            detect_format(cfg)

    def test_raises_on_ambiguous_format(self, tmp_path):
        from navig.migration import detect_format, ConfigMigrationError
        cfg = tmp_path / "ambiguous.yaml"
        cfg.write_text(yaml.dump({"something_else": True}), encoding="utf-8")
        with pytest.raises((ConfigMigrationError, Exception)):
            detect_format(cfg)


class TestExtractWebserverType:
    def test_nginx_from_services_web(self):
        from navig.migration import extract_webserver_type
        config = {"services": {"web": "nginx"}}
        assert extract_webserver_type(config) == "nginx"

    def test_apache_from_services_web(self):
        from navig.migration import extract_webserver_type
        config = {"services": {"web": "apache2"}}
        assert extract_webserver_type(config) == "apache2"

    def test_nginx_case_insensitive(self):
        from navig.migration import extract_webserver_type
        config = {"services": {"web": "Nginx"}}
        assert extract_webserver_type(config) == "nginx"

    def test_raises_when_no_services(self):
        from navig.migration import extract_webserver_type, ConfigMigrationError
        with pytest.raises(ConfigMigrationError):
            extract_webserver_type({})

    def test_raises_when_unknown_webserver(self):
        from navig.migration import extract_webserver_type, ConfigMigrationError
        with pytest.raises(ConfigMigrationError):
            extract_webserver_type({"services": {"web": "iis"}})

    def test_uses_existing_webserver_type(self):
        from navig.migration import extract_webserver_type
        config = {"webserver": {"type": "nginx"}, "services": {"web": "ignored"}}
        assert extract_webserver_type(config) == "nginx"


class TestBackupConfig:
    def test_creates_backup_file(self, tmp_path):
        from navig.migration import backup_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("host: myhost", encoding="utf-8")
        backup_path = backup_config(cfg)
        assert backup_path.exists()
        assert backup_path != cfg

    def test_backup_preserves_content(self, tmp_path):
        from navig.migration import backup_config
        cfg = tmp_path / "config.yaml"
        content = "host: example.com\nport: 22"
        cfg.write_text(content, encoding="utf-8")
        backup_path = backup_config(cfg)
        assert backup_path.read_text(encoding="utf-8") == content


class TestConfigMigrationError:
    def test_is_exception(self):
        from navig.migration import ConfigMigrationError
        err = ConfigMigrationError("test message")
        assert isinstance(err, Exception)
        assert str(err) == "test message"


# ---------------------------------------------------------------------------
# ai_context — ErrorLog
# ---------------------------------------------------------------------------

class TestErrorLog:
    def _make_log(self):
        from navig.ai_context import ErrorLog
        return ErrorLog(
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            category="network",
            command="navig run 'ls'",
            error="Connection refused",
            context={"server": "prod"},
        )

    def test_to_dict_keys(self):
        log = self._make_log()
        d = log.to_dict()
        for key in ("timestamp", "category", "command", "error", "context"):
            assert key in d

    def test_to_dict_timestamp_is_iso_string(self):
        log = self._make_log()
        ts = log.to_dict()["timestamp"]
        assert isinstance(ts, str)
        assert "2024" in ts

    def test_to_dict_category(self):
        log = self._make_log()
        assert log.to_dict()["category"] == "network"

    def test_from_dict_roundtrip(self):
        from navig.ai_context import ErrorLog
        original = self._make_log()
        d = original.to_dict()
        restored = ErrorLog.from_dict(d)
        assert restored.category == original.category
        assert restored.command == original.command
        assert restored.error == original.error

    def test_from_dict_context_defaults_to_empty(self):
        from navig.ai_context import ErrorLog
        d = {
            "timestamp": "2024-01-15T10:30:00",
            "category": "file",
            "command": "navig file show",
            "error": "Not found",
            # no context key
        }
        log = ErrorLog.from_dict(d)
        assert log.context == {}


class TestAIContextManager:
    def test_instantiates_with_tmp_path(self, tmp_path):
        from navig.ai_context import AIContextManager
        mgr = AIContextManager(config_dir=tmp_path)
        assert mgr.config_dir == tmp_path

    def test_creates_config_dir_if_missing(self, tmp_path):
        from navig.ai_context import AIContextManager
        new_dir = tmp_path / "newdir"
        mgr = AIContextManager(config_dir=new_dir)
        assert new_dir.exists()

    def test_max_error_logs_constant(self):
        from navig.ai_context import AIContextManager
        assert AIContextManager.MAX_ERROR_LOGS == 100

    def test_get_ai_context_manager_returns_instance(self, tmp_path):
        from navig.ai_context import get_ai_context_manager, AIContextManager
        with patch("navig.platform.paths.config_dir", return_value=tmp_path):
            mgr = get_ai_context_manager()
        assert isinstance(mgr, AIContextManager)


# ---------------------------------------------------------------------------
# assistant_hooks — CommandTimer
# ---------------------------------------------------------------------------

class TestCommandTimer:
    def test_duration_is_zero_before_use(self):
        from navig.assistant_hooks import CommandTimer
        timer = CommandTimer()
        assert timer.duration == 0.0

    def test_duration_is_positive_after_use(self):
        from navig.assistant_hooks import CommandTimer
        with CommandTimer() as timer:
            time.sleep(0.01)
        assert timer.duration >= 0.01

    def test_context_manager_returns_self(self):
        from navig.assistant_hooks import CommandTimer
        timer = CommandTimer()
        with timer as t:
            assert t is timer

    def test_context_manager_does_not_suppress_exceptions(self):
        from navig.assistant_hooks import CommandTimer
        with pytest.raises(ValueError):
            with CommandTimer():
                raise ValueError("test error")

    def test_duration_after_exception_is_set(self):
        from navig.assistant_hooks import CommandTimer
        timer = CommandTimer()
        try:
            with timer:
                time.sleep(0.01)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # duration should still be measured
        assert timer.duration >= 0.01


class TestPreExecutionCheck:
    def test_returns_bool(self):
        from navig.assistant_hooks import pre_execution_check
        ctx = {}
        result = pre_execution_check(ctx, "navig run 'ls'", {})
        assert isinstance(result, bool)

    def test_empty_command_does_not_crash(self):
        from navig.assistant_hooks import pre_execution_check
        result = pre_execution_check({}, "", {})
        assert isinstance(result, bool)


class TestPostExecutionLog:
    def test_does_not_raise(self):
        from navig.assistant_hooks import post_execution_log
        # Should be a best-effort log operation (exit_code=0 means success)
        post_execution_log({}, "navig run 'ls'", 0, "output", "", 100.0)

    def test_accepts_failure_exit_code(self):
        from navig.assistant_hooks import post_execution_log
        post_execution_log({}, "navig run 'bad'", 1, "", "error msg", 50.0)
