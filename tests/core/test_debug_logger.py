"""Tests for navig.debug_logger — DebugLogger structured audit log."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.debug_logger import DebugLogger, get_debug_logger


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _make_logger(tmp_path: Path) -> DebugLogger:
    """Create a DebugLogger writing to a temp file."""
    log_file = tmp_path / "debug.log"
    logger = DebugLogger(log_path=log_file, max_size_mb=1, max_files=2, truncate_output_kb=1)
    return logger


def _read_log(tmp_path: Path) -> str:
    log_file = tmp_path / "debug.log"
    if not log_file.exists():
        return ""
    return log_file.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────
# Construction
# ──────────────────────────────────────────────────────────────


class TestDebugLoggerConstruction:
    def test_creates_log_file(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_start("test", {})
        assert (tmp_path / "debug.log").exists()
        dl.close()

    def test_accepts_string_path(self, tmp_path):
        log_path = tmp_path / "str_path.log"
        dl = DebugLogger(log_path=str(log_path))
        dl.log_command_start("cmd", {})
        assert log_path.exists()
        dl.close()

    def test_close_is_safe_to_call_multiple_times(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.close()
        dl.close()  # Second close must not raise


# ──────────────────────────────────────────────────────────────
# Timestamp
# ──────────────────────────────────────────────────────────────


class TestTimestamp:
    def test_timestamp_format(self, tmp_path):
        dl = _make_logger(tmp_path)
        ts = dl._timestamp()
        # ISO 8601 format: YYYY-MM-DDTHH:MM:SS.mmmZ
        assert "T" in ts
        assert ts.endswith("Z")
        assert len(ts) == 24  # 2024-01-15T10:30:00.123Z
        dl.close()


# ──────────────────────────────────────────────────────────────
# Truncation
# ──────────────────────────────────────────────────────────────


class TestTruncation:
    def test_short_output_not_truncated(self, tmp_path):
        dl = _make_logger(tmp_path)
        short = "hello world"
        assert dl._truncate(short) == short
        dl.close()

    def test_long_output_truncated(self, tmp_path):
        dl = _make_logger(tmp_path)
        # truncate_output_kb=1 → 1024 bytes
        long_output = "x" * 2000
        result = dl._truncate(long_output)
        assert "TRUNCATED" in result
        assert len(result) < len(long_output)
        dl.close()

    def test_empty_output_returned_unchanged(self, tmp_path):
        dl = _make_logger(tmp_path)
        assert dl._truncate("") == ""
        dl.close()


# ──────────────────────────────────────────────────────────────
# log_command_start
# ──────────────────────────────────────────────────────────────


class TestLogCommandStart:
    def test_writes_to_log(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_start("navig host list", {"verbose": True})
        dl.close()
        content = _read_log(tmp_path)
        assert "COMMAND START" in content
        assert "navig host list" in content

    def test_includes_args(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_start("run", {"host": "prod"})
        dl.close()
        assert "prod" in _read_log(tmp_path)


# ──────────────────────────────────────────────────────────────
# log_ssh_command
# ──────────────────────────────────────────────────────────────


class TestLogSshCommand:
    def test_writes_ssh_command(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_ssh_command("10.0.0.1", 22, "admin", "ls -la")
        dl.close()
        content = _read_log(tmp_path)
        assert "SSH COMMAND" in content
        assert "10.0.0.1" in content

    def test_includes_method(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_ssh_command("host", 22, "user", "cmd", method="paramiko")
        dl.close()
        assert "paramiko" in _read_log(tmp_path)


# ──────────────────────────────────────────────────────────────
# log_ssh_result
# ──────────────────────────────────────────────────────────────


class TestLogSshResult:
    def test_success_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_ssh_result(True, "output text", duration_ms=42.0)
        dl.close()
        content = _read_log(tmp_path)
        assert "SUCCESS" in content
        assert "output text" in content

    def test_failure_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_ssh_result(False, "", "permission denied", duration_ms=10.0)
        dl.close()
        content = _read_log(tmp_path)
        assert "FAILED" in content
        assert "permission denied" in content


# ──────────────────────────────────────────────────────────────
# log_command_end
# ──────────────────────────────────────────────────────────────


class TestLogCommandEnd:
    def test_end_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_end(True, "all done")
        dl.close()
        content = _read_log(tmp_path)
        assert "COMMAND END" in content
        assert "SUCCESS" in content

    def test_failed_command_end(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_end(False, "timeout")
        dl.close()
        content = _read_log(tmp_path)
        assert "FAILED" in content

    def test_duration_included_when_start_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_command_start("cmd", {})
        dl.log_command_end(True)
        dl.close()
        assert "Duration" in _read_log(tmp_path)


# ──────────────────────────────────────────────────────────────
# log_error
# ──────────────────────────────────────────────────────────────


class TestLogError:
    def test_logs_exception(self, tmp_path):
        dl = _make_logger(tmp_path)
        try:
            raise ValueError("something broke")
        except ValueError as exc:
            dl.log_error(exc, context="unit test")
        dl.close()
        content = _read_log(tmp_path)
        assert "ERROR" in content
        assert "ValueError" in content

    def test_logs_context(self, tmp_path):
        dl = _make_logger(tmp_path)
        try:
            raise RuntimeError("oops")
        except RuntimeError as exc:
            dl.log_error(exc, context="during deploy")
        dl.close()
        assert "during deploy" in _read_log(tmp_path)


# ──────────────────────────────────────────────────────────────
# log_operation
# ──────────────────────────────────────────────────────────────


class TestLogOperation:
    def test_operation_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_operation("file_upload", {"file": "config.yaml", "size": 1024})
        dl.close()
        content = _read_log(tmp_path)
        assert "file_upload" in content
        assert "config.yaml" in content

    def test_failed_operation_logged(self, tmp_path):
        dl = _make_logger(tmp_path)
        dl.log_operation("db_query", {"query": "SELECT *"}, success=False)
        dl.close()
        content = _read_log(tmp_path)
        assert "FAILED" in content


# ──────────────────────────────────────────────────────────────
# Redaction
# ──────────────────────────────────────────────────────────────


class TestRedaction:
    def test_redact_sensitive_data_callable(self, tmp_path):
        dl = _make_logger(tmp_path)
        # Should not raise; actual redaction depends on navig.core.security
        result = dl._redact_sensitive_data("some text with a token")
        assert isinstance(result, str)
        dl.close()


# ──────────────────────────────────────────────────────────────
# get_debug_logger
# ──────────────────────────────────────────────────────────────


class TestGetDebugLogger:
    def test_returns_logger_instance(self):
        import logging
        result = get_debug_logger()
        assert isinstance(result, logging.Logger)

    def test_returns_same_logger(self):
        import logging
        l1 = get_debug_logger()
        l2 = get_debug_logger()
        assert l1.name == l2.name
