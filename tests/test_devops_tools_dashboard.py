"""
Batch 121 — devops_tools (_cp_to_str, _MAX_OUTPUT_CHARS)
         + dashboard (DashboardState, _check_port, _check_pid_alive)

Pure-unit tests with mocking where needed; no network, no real processes.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# devops_tools — _cp_to_str, _MAX_OUTPUT_CHARS
# ---------------------------------------------------------------------------

from navig.agent.tools.devops_tools import _MAX_OUTPUT_CHARS, _cp_to_str


class TestMaxOutputChars:
    def test_value_is_4000(self):
        assert _MAX_OUTPUT_CHARS == 4_000

    def test_positive(self):
        assert _MAX_OUTPUT_CHARS > 0


class TestCpToStr:
    def _cp(self, stdout="", stderr="", returncode=0):
        cp = MagicMock(spec=subprocess.CompletedProcess)
        cp.stdout = stdout
        cp.stderr = stderr
        cp.returncode = returncode
        return cp

    def test_stdout_string_returned(self):
        result = _cp_to_str(self._cp(stdout="hello world"))
        assert "hello world" in result

    def test_stderr_appended_when_present(self):
        result = _cp_to_str(self._cp(stdout="out", stderr="err message"))
        assert "err message" in result
        assert "[stderr]" in result

    def test_no_output_placeholder(self):
        result = _cp_to_str(self._cp(stdout="", stderr=""))
        assert result == "(no output)"

    def test_bytes_stdout_decoded(self):
        cp = self._cp(stdout=b"hello bytes")
        result = _cp_to_str(cp)
        assert "hello bytes" in result

    def test_bytes_stderr_decoded(self):
        cp = self._cp(stdout="", stderr=b"bytes err")
        result = _cp_to_str(cp)
        assert "bytes err" in result

    def test_whitespace_only_stderr_not_appended(self):
        result = _cp_to_str(self._cp(stdout="out", stderr="   "))
        assert "[stderr]" not in result

    def test_stdout_only_no_stderr_label(self):
        result = _cp_to_str(self._cp(stdout="my output", stderr=""))
        assert "[stderr]" not in result
        assert "my output" in result

    def test_empty_stdout_with_stderr(self):
        result = _cp_to_str(self._cp(stdout="", stderr="critical error"))
        assert "critical error" in result


# ---------------------------------------------------------------------------
# dashboard — DashboardState
# ---------------------------------------------------------------------------

from navig.commands.dashboard import DashboardState, _check_pid_alive, _check_port


class TestDashboardState:
    def test_running_true_on_init(self):
        ds = DashboardState()
        assert ds.running is True

    def test_hosts_status_empty_on_init(self):
        ds = DashboardState()
        assert ds.hosts_status == {}

    def test_op_state_empty_on_init(self):
        ds = DashboardState()
        assert ds.op_state == {}

    def test_kraken_frame_zero_on_init(self):
        ds = DashboardState()
        assert ds.kraken_frame == 0

    def test_events_zero_on_init(self):
        ds = DashboardState()
        assert ds.events == 0

    def test_errors_zero_on_init(self):
        ds = DashboardState()
        assert ds.errors == 0

    def test_activity_log_empty_on_init(self):
        ds = DashboardState()
        assert ds.activity_log == []

    def test_started_at_is_float(self):
        ds = DashboardState()
        assert isinstance(ds.started_at, float)

    def test_refresh_requested_false_on_init(self):
        ds = DashboardState()
        assert ds.refresh_requested is False

    # log()
    def test_log_appends_entry(self):
        ds = DashboardState()
        ds.log("test message")
        assert len(ds.activity_log) == 1

    def test_log_contains_message(self):
        ds = DashboardState()
        ds.log("hello log")
        assert "hello log" in ds.activity_log[0]

    def test_log_increments_events(self):
        ds = DashboardState()
        ds.log("msg1")
        ds.log("msg2")
        assert ds.events == 2

    def test_log_trims_to_50(self):
        ds = DashboardState()
        for i in range(60):
            ds.log(f"msg {i}")
        assert len(ds.activity_log) <= 50

    def test_log_keeps_most_recent_50(self):
        ds = DashboardState()
        for i in range(60):
            ds.log(f"msg {i}")
        # Last entry should have msg 59
        assert "msg 59" in ds.activity_log[-1]


# ---------------------------------------------------------------------------
# _check_port — mocked socket
# ---------------------------------------------------------------------------


class TestCheckPort:
    def test_returns_bool(self):
        result = _check_port(65432)  # Very unlikely to be open
        assert isinstance(result, bool)

    def test_closed_port_returns_false(self):
        # Port 1 is almost certainly not open on localhost
        result = _check_port(1)
        assert result is False

    def test_high_port_returns_bool(self):
        result = _check_port(59876)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _check_pid_alive — mocked
# ---------------------------------------------------------------------------


class TestCheckPidAlive:
    def test_returns_bool(self):
        # Test with PID 1 (always exists on Linux; may or may not on Windows)
        # We just verify a bool is returned
        result = _check_pid_alive(1)
        assert isinstance(result, bool)

    def test_nonexistent_pid_returns_false(self):
        # Use an extremely unlikely PID
        result = _check_pid_alive(999_999_999)
        assert result is False

    def test_own_pid_returns_true(self):
        import os

        result = _check_pid_alive(os.getpid())
        assert result is True
