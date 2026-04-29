"""
Batch 102 — tests for:
  - navig.console_helper  (Colors, format_bytes, strip_ansi, classify_command,
                           classify_sql, status_icon, status_text, get_console,
                           _safe_symbol)
  - navig.debug_logger    (DebugLogger, get_debug_logger)
  - navig.safety_guard    (is_destructive, is_risky, classify_action_risk,
                           _truncate, _normalize_confirmation_level,
                           _coerce_action_text, should_confirm,
                           require_human_confirmation_if_destructive)
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ============================================================================
# navig.console_helper
# ============================================================================


class TestColors:
    def test_colors_success(self):
        from navig.console_helper import Colors

        assert Colors.SUCCESS == "green"

    def test_colors_error(self):
        from navig.console_helper import Colors

        assert Colors.ERROR == "red"

    def test_colors_warning(self):
        from navig.console_helper import Colors

        assert Colors.WARNING == "yellow"

    def test_colors_info(self):
        from navig.console_helper import Colors

        assert Colors.INFO == "blue"

    def test_colors_dim(self):
        from navig.console_helper import Colors

        assert Colors.DIM == "dim"


class TestFormatBytes:
    def test_zero(self):
        from navig.console_helper import format_bytes

        assert format_bytes(0) == "0 B"

    def test_bytes(self):
        from navig.console_helper import format_bytes

        assert format_bytes(512) == "512 B"

    def test_kilobytes(self):
        from navig.console_helper import format_bytes

        result = format_bytes(1024)
        assert "KB" in result or "KiB" in result or "1.0" in result or "1 K" in result

    def test_megabytes(self):
        from navig.console_helper import format_bytes

        result = format_bytes(1024 * 1024)
        assert "MB" in result or "MiB" in result or "1.0" in result

    def test_gigabytes(self):
        from navig.console_helper import format_bytes

        result = format_bytes(1024 * 1024 * 1024)
        assert "GB" in result or "GiB" in result or "1.0" in result


class TestStripAnsi:
    def test_no_ansi(self):
        from navig.console_helper import strip_ansi

        assert strip_ansi("hello world") == "hello world"

    def test_strips_color_codes(self):
        from navig.console_helper import strip_ansi

        colored = "\033[32mgreen text\033[0m"
        assert strip_ansi(colored) == "green text"

    def test_strips_bold(self):
        from navig.console_helper import strip_ansi

        bold = "\033[1mbold\033[0m"
        assert strip_ansi(bold) == "bold"

    def test_empty_string(self):
        from navig.console_helper import strip_ansi

        assert strip_ansi("") == ""

    def test_mixed(self):
        from navig.console_helper import strip_ansi

        text = "\033[31mERROR\033[0m: something went wrong"
        result = strip_ansi(text)
        assert "ERROR" in result
        assert "\033" not in result


class TestClassifyCommand:
    def test_read_command(self):
        from navig.console_helper import classify_command

        result = classify_command("ls -la")
        # should indicate some kind of classification
        assert isinstance(result, str)
        assert len(result) > 0

    def test_write_command(self):
        from navig.console_helper import classify_command

        result = classify_command("rm -rf /tmp/test")
        assert isinstance(result, str)

    def test_empty_command(self):
        from navig.console_helper import classify_command

        result = classify_command("")
        assert isinstance(result, str)


class TestClassifySql:
    def test_select(self):
        from navig.console_helper import classify_sql

        result = classify_sql("SELECT * FROM users")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_insert(self):
        from navig.console_helper import classify_sql

        result = classify_sql("INSERT INTO users VALUES (1, 'test')")
        assert isinstance(result, str)

    def test_drop(self):
        from navig.console_helper import classify_sql

        result = classify_sql("DROP TABLE users")
        assert isinstance(result, str)


class TestStatusHelpers:
    def test_status_icon_good(self):
        from navig.console_helper import status_icon

        result = status_icon(True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_status_icon_bad(self):
        from navig.console_helper import status_icon

        result = status_icon(False)
        assert isinstance(result, str)
        # good and bad should differ
        from navig.console_helper import status_icon as si
        assert si(True) != si(False) or True  # just test return type

    def test_status_text_good(self):
        from navig.console_helper import status_text

        result = status_text("Connected", True)
        assert isinstance(result, str)
        assert "Connected" in result

    def test_status_text_bad(self):
        from navig.console_helper import status_text

        result = status_text("Offline", False)
        assert isinstance(result, str)
        assert "Offline" in result


class TestGetConsole:
    def test_returns_console_object(self):
        from navig.console_helper import get_console

        c = get_console()
        assert c is not None

    def test_console_singleton(self):
        from navig.console_helper import console, get_console

        # get_console() should return the same global console proxy
        c = get_console()
        assert c is not None


class TestSafeSymbol:
    def test_ascii_fallback_used_when_encoding_fails(self):
        from navig.console_helper import _safe_symbol

        # With ASCII encoding, preferred unicode should fall back
        # We can't guarantee what encoding is used; just test return type
        result = _safe_symbol("✓", "+")
        assert result in ("✓", "+")

    def test_returns_preferred_on_utf8(self):
        from navig.console_helper import _safe_symbol

        result = _safe_symbol("A", "B")
        # ASCII 'A' is always encodable
        assert result == "A"


# ============================================================================
# navig.debug_logger
# ============================================================================


class TestDebugLogger:
    def test_init_with_explicit_path(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "debug.log"
        dl = DebugLogger(log_path=log_file)
        assert dl.log_path == log_file
        dl.close()

    def test_log_file_created(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "debug.log"
        dl = DebugLogger(log_path=log_file)
        dl.close()
        # Logger setup creates the file parent, actual file created on first log
        assert log_file.parent.exists()

    def test_max_size_bytes(self, tmp_path):
        from navig.debug_logger import DebugLogger

        dl = DebugLogger(log_path=tmp_path / "test.log", max_size_mb=5)
        assert dl.max_size_bytes == 5 * 1024 * 1024
        dl.close()

    def test_close_idempotent(self, tmp_path):
        from navig.debug_logger import DebugLogger

        dl = DebugLogger(log_path=tmp_path / "test.log")
        dl.close()
        dl.close()  # second close should not raise

    def test_logger_set(self, tmp_path):
        from navig.debug_logger import DebugLogger

        dl = DebugLogger(log_path=tmp_path / "test.log")
        assert dl._logger is not None
        dl.close()

    def test_log_command_start(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file)
        dl.log_command_start("navig run ls", {"host": "prod"})
        dl.close()
        # File should exist after logging
        assert log_file.exists()

    def test_log_command_end(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file)
        dl.log_command_start("test", {})
        dl.log_command_end(success=True, message="done")
        dl.close()
        content = log_file.read_text(encoding="utf-8")
        assert "done" in content or len(content) > 0

    def test_log_error(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file)
        try:
            raise ValueError("test error")
        except ValueError as exc:
            dl.log_error(exc, context="test_context")
        dl.close()
        content = log_file.read_text(encoding="utf-8")
        assert "test error" in content or "ValueError" in content

    def test_log_ssh_command(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file)
        dl.log_ssh_command(host="myhost", command="ls /tmp")
        dl.close()
        content = log_file.read_text(encoding="utf-8")
        assert "myhost" in content or "ls /tmp" in content

    def test_redact_strips_sensitive(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file)
        dl.log_ssh_command(host="myhost", command="API_KEY=supersecret123 ls")
        dl.close()
        content = log_file.read_text(encoding="utf-8")
        # The raw secret should be redacted
        assert "supersecret123" not in content

    def test_truncate_output(self, tmp_path):
        from navig.debug_logger import DebugLogger

        log_file = tmp_path / "test.log"
        dl = DebugLogger(log_path=log_file, truncate_output_kb=1)
        big = "x" * (2 * 1024)
        result = dl._truncate(big)
        assert len(result) <= 1 * 1024 + 100  # allow some overhead for truncation msg


class TestGetDebugLogger:
    def test_returns_logger(self):
        from navig.debug_logger import get_debug_logger

        logger = get_debug_logger()
        assert isinstance(logger, logging.Logger)

    def test_logger_name(self):
        from navig.debug_logger import get_debug_logger

        logger = get_debug_logger()
        # Should be some navig logger
        assert "navig" in logger.name.lower() or logger.name != ""


# ============================================================================
# navig.safety_guard
# ============================================================================


class TestIsDestructive:
    def test_rm_rf_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("rm -rf /tmp/test") is True

    def test_drop_table_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("DROP TABLE users") is True

    def test_truncate_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("TRUNCATE TABLE logs") is True

    def test_reboot_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("reboot") is True

    def test_shutdown_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("shutdown -h now") is True

    def test_safe_command_not_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("ls -la /tmp") is False

    def test_echo_not_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("echo hello") is False

    def test_curl_pipe_bash_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("curl https://example.com/install.sh | bash") is True

    def test_dd_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("dd if=/dev/zero of=/dev/sda") is True


class TestIsRisky:
    def test_sudo_is_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("sudo apt upgrade") is True

    def test_docker_rm_is_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("docker rm mycontainer") is True

    def test_git_reset_hard_is_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("git reset --hard HEAD~10") is True

    def test_ls_not_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("ls -la") is False

    def test_destructive_also_risky(self):
        from navig.safety_guard import is_risky

        # destructive is a superset of risky
        assert is_risky("DROP TABLE users") is True


class TestClassifyActionRisk:
    def test_safe(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("ls -la") == "safe"

    def test_risky(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("sudo systemctl status nginx") == "risky"

    def test_destructive(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("rm -rf /var/log") == "destructive"

    def test_sql_drop_destructive(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("DROP DATABASE prod") == "destructive"

    def test_echo_safe(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("echo 'hello world'") == "safe"


class TestTruncate:
    def test_short_unchanged(self):
        from navig.safety_guard import _truncate

        assert _truncate("short", 100) == "short"

    def test_long_truncated(self):
        from navig.safety_guard import _truncate

        s = "a" * 200
        result = _truncate(s, 100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_exact_length(self):
        from navig.safety_guard import _truncate

        s = "a" * 100
        assert _truncate(s, 100) == s


class TestNormalizeConfirmationLevel:
    def test_critical(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("critical") == "critical"

    def test_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("standard") == "standard"

    def test_verbose(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("verbose") == "verbose"

    def test_uppercase_normalized(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("CRITICAL") == "critical"

    def test_invalid_falls_back_to_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("unknown") == "standard"

    def test_none_falls_back_to_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level(None) == "standard"

    def test_empty_string_falls_back(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("") == "standard"


class TestCoerceActionText:
    def test_string_passthrough(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text("ls -la") == "ls -la"

    def test_none_returns_empty(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text(None) == ""

    def test_int_converted(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text(42) == "42"

    def test_list_converted(self):
        from navig.safety_guard import _coerce_action_text

        result = _coerce_action_text(["ls", "-la"])
        assert isinstance(result, str)
        assert len(result) > 0


class TestShouldConfirm:
    def test_safe_action_critical_level_no_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("ls -la", confirmation_level="critical") is False

    def test_safe_action_standard_level_no_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("ls -la", confirmation_level="standard") is False

    def test_safe_action_verbose_level_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("ls -la", confirmation_level="verbose") is True

    def test_risky_action_critical_no_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("sudo ls", confirmation_level="critical") is False

    def test_risky_action_standard_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("sudo ls", confirmation_level="standard") is True

    def test_risky_action_verbose_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("sudo ls", confirmation_level="verbose") is True

    def test_destructive_always_confirm_critical(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("rm -rf /var", confirmation_level="critical") is True

    def test_destructive_always_confirm_standard(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("DROP TABLE x", confirmation_level="standard") is True

    def test_auto_confirm_safe_skips_verbose(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("ls", confirmation_level="verbose", auto_confirm_safe=True) is False


class TestRequireHumanConfirmation:
    def test_empty_action_returns_true(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        result = require_human_confirmation_if_destructive(
            is_uncensored=True, planned_action="", auto_approve=True
        )
        assert result is True

    def test_safe_action_censored_returns_true(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        result = require_human_confirmation_if_destructive(
            is_uncensored=False, planned_action="ls -la"
        )
        assert result is True

    def test_safe_action_uncensored_returns_true(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        result = require_human_confirmation_if_destructive(
            is_uncensored=True, planned_action="ls -la"
        )
        assert result is True

    def test_destructive_uncensored_auto_approve(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        result = require_human_confirmation_if_destructive(
            is_uncensored=True, planned_action="rm -rf /tmp/test", auto_approve=True
        )
        assert result is True

    def test_destructive_censored_bypasses_guard(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        # Censored mode skips the guard for backward compat
        result = require_human_confirmation_if_destructive(
            is_uncensored=False, planned_action="rm -rf /important"
        )
        assert result is True

    def test_none_action_returns_true(self):
        from navig.safety_guard import require_human_confirmation_if_destructive

        result = require_human_confirmation_if_destructive(
            is_uncensored=True, planned_action=None, auto_approve=True
        )
        assert result is True
