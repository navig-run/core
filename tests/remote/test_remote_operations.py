"""
Tests for navig.remote.RemoteOperations SSH error handling and local bypass.

Covers:
- Local-host bypass (is_local, type=local, localhost/127.0.0.1/::1)
- Graceful RuntimeError when ssh binary is absent (FileNotFoundError)
- Existing TimeoutExpired handling is unaffected
"""

import importlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from navig.remote import RemoteOperations

pytestmark = pytest.mark.integration


@pytest.fixture()
def remote_ops():
    """RemoteOperations instance with a stub config manager."""
    config = MagicMock()
    return RemoteOperations(config)


# ---------------------------------------------------------------------------
# Local-host bypass
# ---------------------------------------------------------------------------


class TestLocalHostBypass:
    """execute_command delegates to execute_local for local targets."""

    @pytest.mark.parametrize(
        "server_config",
        [
            {"host": "localhost", "user": "me"},
            {"host": "127.0.0.1", "user": "me"},
            {"host": "::1", "user": "me"},
            {"host": "remote.example.com", "user": "me", "is_local": True},
            {"host": "remote.example.com", "user": "me", "type": "local"},
        ],
    )
    def test_local_config_runs_locally(self, remote_ops, server_config):
        """execute_command must call execute_local, never subprocess for local hosts."""
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch.object(remote_ops, "execute_local", return_value=fake_result) as mock_local:
            result = remote_ops.execute_command("echo hi", server_config)

        mock_local.assert_called_once_with("echo hi", capture_output=True)
        assert result is fake_result

    def test_remote_config_does_not_run_locally(self, remote_ops):
        """A genuine remote host must NOT call execute_local."""
        server_config = {"host": "remote.example.com", "user": "deploy"}
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch("navig.remote._resolve_ssh_bin", return_value="/usr/bin/ssh"),
            patch("subprocess.run", return_value=fake_result) as mock_run,
            patch.object(remote_ops, "execute_local") as mock_local,
        ):
            remote_ops.execute_command("echo hi", server_config)

        mock_local.assert_not_called()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Missing SSH binary → clear RuntimeError
# ---------------------------------------------------------------------------


class TestMissingSSHBinary:
    """execute_command raises RuntimeError with a helpful message when ssh is absent."""

    def test_raises_runtime_error_when_ssh_missing(self, remote_ops):
        """Missing SSH binary must raise RuntimeError before subprocess is called."""
        server_config = {"host": "remote.example.com", "user": "deploy"}

        with (
            patch(
                "navig.remote._resolve_ssh_bin",
                side_effect=RuntimeError("SSH client not found on PATH. Install OpenSSH."),
            ),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                remote_ops.execute_command("echo hi", server_config)

        msg = str(exc_info.value)
        assert "SSH client not found" in msg
        assert "OpenSSH" in msg

    def test_runtime_error_raised_before_subprocess(self, remote_ops):
        """subprocess.run must NOT be called when ssh binary is missing."""
        server_config = {"host": "remote.example.com", "user": "deploy"}

        with (
            patch(
                "navig.remote._resolve_ssh_bin",
                side_effect=RuntimeError("SSH client not found on PATH. Install OpenSSH."),
            ),
            patch("subprocess.run") as mock_run,
        ):
            with pytest.raises(RuntimeError):
                remote_ops.execute_command("echo hi", server_config)

        mock_run.assert_not_called()

    def test_timeout_still_raises_runtime_error(self, remote_ops):
        """TimeoutExpired handling must remain unaffected by this change."""
        server_config = {"host": "slow.example.com", "user": "deploy"}

        with (
            patch("navig.remote._resolve_ssh_bin", return_value="/usr/bin/ssh"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["ssh"], timeout=30),
            ),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                remote_ops.execute_command("echo hi", server_config)

        assert "timed out" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SSH binary resolution
# ---------------------------------------------------------------------------


class TestSSHBinaryResolution:
    """execute_command prefers the resolved binary path over bare 'ssh'."""

    def test_uses_resolved_ssh_path(self, remote_ops):
        """When shutil.which finds ssh, its path must be used in subprocess args."""
        server_config = {"host": "remote.example.com", "user": "deploy"}
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return fake_result

        with (
            patch("navig.remote._resolve_ssh_bin", return_value="/usr/bin/ssh"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            remote_ops.execute_command("echo hi", server_config)

        assert captured["args"][0] == "/usr/bin/ssh"


class TestRemoteInputValidation:
    """Input validation regressions for command/file remote ops."""

    def test_execute_command_requires_user_and_host(self, remote_ops):
        with pytest.raises(ValueError) as exc_info:
            remote_ops.execute_command("echo hi", {"host": "example.com"})

        assert "must include non-empty 'user' and 'host'" in str(exc_info.value)

    def test_upload_file_requires_user_and_host(self, remote_ops, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x", encoding="utf-8")

        with pytest.raises(ValueError) as exc_info:
            remote_ops.upload_file(src, "/tmp/a.txt", {"user": "deploy"})

        assert "must include non-empty 'user' and 'host'" in str(exc_info.value)


class TestRemoteTimeoutEnvParsing:
    """Module-level timeout parsing should be robust to malformed env values."""

    def test_invalid_timeout_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("NAVIG_SSH_TIMEOUT", "not-a-number")

        import navig.remote as remote_mod

        importlib.reload(remote_mod)
        assert remote_mod._SSH_TIMEOUT == 30
