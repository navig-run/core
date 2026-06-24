"""Tests for navig.deploy.health — HealthChecker."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from navig.deploy.health import HealthChecker
from navig.deploy.models import HealthConfig


@dataclass
class FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _checker(
    url: str | None = "http://localhost/health",
    command: str | None = None,
    method: str = "GET",
    expected_status: int = 200,
    retries: int = 1,
    interval: int = 0,
    dry_run: bool = False,
    remote: object | None = None,
) -> HealthChecker:
    cfg = HealthConfig(
        url=url,
        command=command,
        method=method,
        expected_status=expected_status,
        retries=retries,
        interval_seconds=interval,
        timeout_seconds=5,
    )
    if remote is None:
        remote = MagicMock()
    return HealthChecker(cfg, server_config={}, remote_ops=remote, dry_run=dry_run)


class TestDryRun:
    def test_dry_run_always_returns_true(self):
        c = _checker(dry_run=True)
        ok, msg = c.check()
        assert ok is True
        assert "DRY RUN" in msg

    def test_dry_run_includes_url(self):
        c = _checker(url="http://myapp/health", dry_run=True)
        ok, msg = c.check()
        assert "http://myapp/health" in msg


class TestNoConfig:
    def test_no_url_no_command_returns_skipped(self):
        c = _checker(url=None, command=None)
        ok, msg = c.check()
        assert ok is True
        assert "skipped" in msg.lower()


class TestHttpCheck:
    def test_successful_http_check(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=0, stdout="200")
        c = _checker(remote=remote, retries=1)
        ok, msg = c.check()
        assert ok is True
        assert "200" in msg

    def test_failed_status_code(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=0, stdout="503")
        c = _checker(remote=remote, retries=1, expected_status=200)
        ok, msg = c.check()
        assert ok is False
        assert "503" in msg

    def test_curl_error_exit_code(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=7, stdout="")
        c = _checker(remote=remote, retries=1)
        ok, msg = c.check()
        assert ok is False
        assert "curl failed" in msg

    def test_unexpected_curl_output(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=0, stdout="not_a_number")
        c = _checker(remote=remote, retries=1)
        ok, msg = c.check()
        assert ok is False
        assert "Unexpected" in msg

    def test_invalid_http_method_rejected(self):
        remote = MagicMock()
        c = _checker(remote=remote, method="GET && curl evil.com", retries=1)
        ok, msg = c.check()
        assert ok is False
        assert "Invalid HTTP method" in msg


class TestCommandCheck:
    def test_command_exit_zero_is_healthy(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=0, stdout="")
        c = _checker(url=None, command="pgrep myapp", remote=remote, retries=1)
        ok, msg = c.check()
        assert ok is True
        assert "exit 0" in msg

    def test_command_non_zero_is_unhealthy(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=1, stdout="")
        c = _checker(url=None, command="pgrep missing", remote=remote, retries=1)
        ok, msg = c.check()
        assert ok is False


class TestRetryLogic:
    def test_retries_on_failure_then_succeeds(self):
        remote = MagicMock()
        remote.execute_command.side_effect = [
            FakeResult(returncode=0, stdout="503"),
            FakeResult(returncode=0, stdout="200"),
        ]
        with patch("navig.deploy.health.time.sleep"):
            c = _checker(remote=remote, retries=2, interval=0)
            ok, msg = c.check()
        assert ok is True

    def test_all_retries_exhausted(self):
        remote = MagicMock()
        remote.execute_command.return_value = FakeResult(returncode=0, stdout="503")
        with patch("navig.deploy.health.time.sleep"):
            c = _checker(remote=remote, retries=3, interval=1)
            ok, msg = c.check()
        assert ok is False
        assert "All 3" in msg
