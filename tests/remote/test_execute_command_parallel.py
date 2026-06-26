"""
tests/remote/test_execute_command_parallel.py

Unit tests for RemoteOperations.execute_command_parallel().
All tests mock execute_command() — no real SSH is ever called.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from navig.remote import RemoteOperations

REQUIRED_KEYS = {"host", "stdout", "stderr", "returncode", "latency_ms", "error"}


@pytest.fixture()
def remote_ops():
    config = MagicMock()
    config.load_host_config.side_effect = lambda name: {"host": name, "user": "ci"}
    return RemoteOperations(config)


def _ok_result(stdout: str = "ok") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class TestExecuteCommandParallel:
    def test_all_hosts_success(self, remote_ops):
        hosts = ["a", "b", "c"]
        with patch.object(remote_ops, "execute_command", return_value=_ok_result("done")):
            results = remote_ops.execute_command_parallel("echo hi", hosts)

        assert len(results) == 3
        returned_hosts = {r["host"] for r in results}
        assert returned_hosts == set(hosts)
        for r in results:
            assert r["returncode"] == 0
            assert r["error"] is None

    def test_partial_failure_captured(self, remote_ops):
        def _side_effect(cmd, cfg, **kw):
            if cfg["host"] == "bad":
                raise RuntimeError("connection refused")
            return _ok_result()

        hosts = ["good1", "bad", "good2"]
        with patch.object(remote_ops, "execute_command", side_effect=_side_effect):
            results = remote_ops.execute_command_parallel("ls", hosts)

        by_host = {r["host"]: r for r in results}
        assert by_host["bad"]["error"] == "connection refused"
        assert by_host["bad"]["returncode"] == -1
        assert by_host["good1"]["error"] is None
        assert by_host["good2"]["error"] is None

    def test_empty_host_list_returns_empty(self, remote_ops):
        results = remote_ops.execute_command_parallel("echo hi", [])
        assert results == []

    def test_result_has_required_keys(self, remote_ops):
        with patch.object(remote_ops, "execute_command", return_value=_ok_result()):
            results = remote_ops.execute_command_parallel("pwd", ["host1"])

        assert len(results) == 1
        assert REQUIRED_KEYS.issubset(results[0].keys())

    def test_never_raises_when_all_fail(self, remote_ops):
        with patch.object(remote_ops, "execute_command", side_effect=OSError("boom")):
            results = remote_ops.execute_command_parallel("cmd", ["x", "y"])

        assert len(results) == 2
        for r in results:
            assert r["error"] is not None
            assert r["returncode"] == -1

    def test_latency_ms_is_nonnegative_int(self, remote_ops):
        with patch.object(remote_ops, "execute_command", return_value=_ok_result()):
            results = remote_ops.execute_command_parallel("date", ["srv"])

        assert isinstance(results[0]["latency_ms"], int)
        assert results[0]["latency_ms"] >= 0

    def test_stdout_captured_from_result(self, remote_ops):
        with patch.object(remote_ops, "execute_command", return_value=_ok_result("hello world")):
            results = remote_ops.execute_command_parallel("echo hello world", ["host1"])

        assert results[0]["stdout"] == "hello world"

    def test_single_host_returns_one_result(self, remote_ops):
        with patch.object(remote_ops, "execute_command", return_value=_ok_result()):
            results = remote_ops.execute_command_parallel("uptime", ["only-host"])

        assert len(results) == 1
        assert results[0]["host"] == "only-host"
