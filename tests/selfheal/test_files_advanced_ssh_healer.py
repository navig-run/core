"""
Batch 121: tests for
  - navig/commands/files_advanced.py  (delete/mkdir/chmod/chown dry_run & execute)
  - navig/selfheal/ssh_healer.py      (HealResult, SSHHealer, _sanitize_ssh_verbose)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared by all files_advanced tests
# ---------------------------------------------------------------------------

import navig.commands.files_advanced as _fa
import navig.console_helper as _ch


def _cmd_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _make_patches(remote_calls: list):
    """Return a context-manager factory tuple for the three mandatory patches."""
    mock_cm = MagicMock()
    mock_cm.load_server_config.return_value = {"host": "srv"}

    mock_remote_ops = MagicMock()
    mock_remote_ops.execute_command.side_effect = remote_calls

    return mock_cm, mock_remote_ops


# ---------------------------------------------------------------------------
# delete_file_cmd
# ---------------------------------------------------------------------------


class TestDeleteFileCmd:
    def _run(self, remote, options, remote_calls):
        mock_cm, mock_remote_ops = _make_patches(remote_calls)
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "warning", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
            patch.object(_ch, "confirm_action", create=True, return_value=True),
        ):
            return _fa.delete_file_cmd(remote, options)

    def test_not_found_returns_false(self):
        result = self._run(
            "/tmp/nope",
            {},
            [_cmd_result(stdout="not_found")],
        )
        assert result is False

    def test_dry_run_file_returns_true(self):
        result = self._run(
            "/tmp/f.txt",
            {"dry_run": True},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="file"),
            ],
        )
        assert result is True

    def test_dry_run_json(self):
        with patch.object(_ch, "raw_print", create=True) as mock_rp:
            result = self._run(
                "/tmp/f.txt",
                {"dry_run": True, "json": True},
                [
                    _cmd_result(stdout="exists"),
                    _cmd_result(stdout="file"),
                ],
            )
        assert result is True

    def test_directory_without_recursive_returns_false(self):
        result = self._run(
            "/tmp/mydir",
            {},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="dir"),
            ],
        )
        assert result is False

    def test_directory_force_delete_returns_true(self):
        result = self._run(
            "/tmp/mydir",
            {"recursive": True, "force": True},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="dir"),
                _cmd_result(returncode=0),
            ],
        )
        assert result is True

    def test_file_force_delete_success(self):
        result = self._run(
            "/tmp/f.txt",
            {"force": True},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="file"),
                _cmd_result(returncode=0),
            ],
        )
        assert result is True

    def test_file_delete_failure_returns_false(self):
        result = self._run(
            "/tmp/f.txt",
            {"force": True},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="file"),
                _cmd_result(returncode=1, stderr="permission denied"),
            ],
        )
        assert result is False

    def test_json_mode_no_force_returns_false(self):
        result = self._run(
            "/tmp/f.txt",
            {"json": True},
            [
                _cmd_result(stdout="exists"),
                _cmd_result(stdout="file"),
            ],
        )
        assert result is False


# ---------------------------------------------------------------------------
# mkdir_cmd
# ---------------------------------------------------------------------------


class TestMkdirCmd:
    def _run(self, remote, options, remote_calls=None):
        mock_cm, mock_remote_ops = _make_patches(remote_calls or [])
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
        ):
            return _fa.mkdir_cmd(remote, options)

    def test_dry_run_returns_true(self):
        assert self._run("/srv/newdir", {"dry_run": True}) is True

    def test_dry_run_json(self):
        assert self._run("/srv/newdir", {"dry_run": True, "json": True}) is True

    def test_invalid_mode_returns_false(self):
        assert self._run("/srv/x", {"mode": "abc"}) is False

    def test_success(self):
        assert (
            self._run(
                "/srv/newdir",
                {"mode": "755"},
                [_cmd_result(returncode=0)],
            )
            is True
        )

    def test_failure_returns_false(self):
        assert (
            self._run(
                "/srv/newdir",
                {"mode": "755"},
                [_cmd_result(returncode=1, stderr="permission denied")],
            )
            is False
        )

    def test_parents_flag(self):
        mock_cm, mock_remote_ops = _make_patches([_cmd_result(returncode=0)])
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
        ):
            _fa.mkdir_cmd("/deep/path", {"parents": True, "mode": "755"})
        cmd_used = mock_remote_ops.execute_command.call_args[0][0]
        assert "-p" in cmd_used


# ---------------------------------------------------------------------------
# chmod_cmd
# ---------------------------------------------------------------------------


class TestChmodCmd:
    def _run(self, remote, mode, options, remote_calls=None):
        mock_cm, mock_remote_ops = _make_patches(remote_calls or [])
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
        ):
            return _fa.chmod_cmd(remote, mode, options)

    def test_invalid_mode_returns_false(self):
        assert self._run("/tmp/f", "xyz", {}) is False

    def test_dry_run_returns_true(self):
        assert self._run("/tmp/f", "644", {"dry_run": True}) is True

    def test_success(self):
        assert self._run("/tmp/f", "644", {}, [_cmd_result(returncode=0)]) is True

    def test_failure_returns_false(self):
        assert self._run("/tmp/f", "644", {}, [_cmd_result(returncode=1, stderr="err")]) is False

    def test_recursive_flag_in_cmd(self):
        mock_cm, mock_remote_ops = _make_patches([_cmd_result(returncode=0)])
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
        ):
            _fa.chmod_cmd("/tmp/dir", "755", {"recursive": True})
        cmd_used = mock_remote_ops.execute_command.call_args[0][0]
        assert "-R" in cmd_used


# ---------------------------------------------------------------------------
# chown_cmd
# ---------------------------------------------------------------------------


class TestChownCmd:
    def _run(self, remote, owner, options, remote_calls=None):
        mock_cm, mock_remote_ops = _make_patches(remote_calls or [])
        with (
            patch("navig.config.get_config_manager", return_value=mock_cm),
            patch("navig.remote.RemoteOperations", return_value=mock_remote_ops),
            patch("navig.cli.recovery.require_active_server", return_value="myhost"),
            patch.object(_ch, "error", create=True),
            patch.object(_ch, "success", create=True),
            patch.object(_ch, "info", create=True),
            patch.object(_ch, "raw_print", create=True),
        ):
            return _fa.chown_cmd(remote, owner, options)

    def test_dry_run_returns_true(self):
        assert self._run("/tmp/f", "root:root", {"dry_run": True}) is True

    def test_success(self):
        assert self._run("/tmp/f", "www-data", {}, [_cmd_result(returncode=0)]) is True

    def test_failure_returns_false(self):
        assert self._run("/tmp/f", "root", {}, [_cmd_result(returncode=1, stderr="err")]) is False


# ---------------------------------------------------------------------------
# navig.selfheal.ssh_healer
# ---------------------------------------------------------------------------

from navig.selfheal.ssh_healer import (
    HealResult,
    SSHHealer,
    _sanitize_ssh_verbose,
    _LOCALHOST_ALIASES,
)


class TestHealResult:
    def test_defaults(self):
        r = HealResult(status="resolved", message="ok")
        assert r.should_retry is False
        assert r.detail == ""

    def test_with_retry(self):
        r = HealResult(status="partial", message="try again", should_retry=True)
        assert r.should_retry is True

    def test_status_values(self):
        for s in ("resolved", "partial", "failed"):
            r = HealResult(status=s, message="x")  # type: ignore[arg-type]
            assert r.status == s


class TestLocalhostAliases:
    def test_contains_loopback(self):
        assert "127.0.0.1" in _LOCALHOST_ALIASES
        assert "localhost" in _LOCALHOST_ALIASES
        assert "::1" in _LOCALHOST_ALIASES


class TestSanitizeSshVerbose:
    def test_removes_plain_debug1(self):
        output = "\n".join(
            [
                "debug1: boring detail",
                "debug1: Connecting to host",
                "Permission denied (publickey)",
                "debug1: cipher: aes256-gcm",
            ]
        )
        result = _sanitize_ssh_verbose(output)
        assert "Permission denied" in result
        assert "Connecting" in result
        # Plain boring debug1 should be stripped
        assert "boring detail" not in result

    def test_returns_last_15_lines(self):
        long_output = "\n".join([f"line{i}" for i in range(50)])
        result = _sanitize_ssh_verbose(long_output)
        lines = result.splitlines()
        assert len(lines) <= 15


class TestReadPublicKey:
    def test_returns_not_found_when_missing(self, tmp_path, monkeypatch):
        # Point _DEFAULT_SSH_KEY_PATH to a tmp path that doesn't have .pub
        import navig.selfheal.ssh_healer as _mod
        monkeypatch.setattr(_mod, "_DEFAULT_SSH_KEY_PATH", tmp_path / "id_ed25519")
        result = SSHHealer._read_public_key()
        assert "not found" in result

    def test_returns_key_content(self, tmp_path, monkeypatch):
        import navig.selfheal.ssh_healer as _mod
        key_path = tmp_path / "id_ed25519"
        pub_path = key_path.with_suffix(".pub")
        pub_path.write_text("ssh-ed25519 AAAA testkey", encoding="utf-8")
        monkeypatch.setattr(_mod, "_DEFAULT_SSH_KEY_PATH", key_path)
        result = SSHHealer._read_public_key()
        assert result == "ssh-ed25519 AAAA testkey"


class TestKeyscanAndTrust:
    def test_localhost_returns_partial(self):
        healer = SSHHealer()
        result = asyncio.run(healer.keyscan_and_trust("127.0.0.1"))
        assert result.status == "partial"
        assert result.should_retry is False

    def test_localhost_alias(self):
        healer = SSHHealer()
        result = asyncio.run(healer.keyscan_and_trust("localhost"))
        assert result.status == "partial"

    def test_ssh_keyscan_not_found(self, monkeypatch):
        healer = SSHHealer()

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError("ssh-keyscan not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        result = asyncio.run(healer.keyscan_and_trust("remotehost"))
        assert result.status == "failed"
        assert "ssh-keyscan" in result.message

    def test_keyscan_timeout(self, monkeypatch):
        healer = SSHHealer()

        async def _fake_exec(*args, **kwargs):
            raise asyncio.TimeoutError()

        # Simulate TimeoutError raised inside asyncio.wait_for
        async def _mock_wait_for(coro, timeout):
            raise asyncio.TimeoutError()

        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock()
        proc_mock.returncode = 0

        async def _fake_exec2(*args, **kwargs):
            return proc_mock

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec2)
        monkeypatch.setattr(asyncio, "wait_for", _mock_wait_for)
        result = asyncio.run(healer.keyscan_and_trust("remotehost"))
        assert result.status == "failed"
        assert "timed out" in result.message

    def test_keyscan_success(self, monkeypatch, tmp_path):
        import navig.selfheal.ssh_healer as _mod

        known_hosts = tmp_path / "known_hosts"
        monkeypatch.setattr(_mod, "_KNOWN_HOSTS_PATH", known_hosts)

        proc_mock = MagicMock()
        proc_mock.returncode = 0

        async def _fake_communicate():
            return (b"ssh-ed25519 AAAA hostkey\n", b"")

        proc_mock.communicate = _fake_communicate

        async def _fake_exec(*args, **kwargs):
            return proc_mock

        async def _fake_wait_for(coro, timeout):
            return await coro

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        healer = SSHHealer()
        result = asyncio.run(healer.keyscan_and_trust("goodhost"))
        assert result.status == "resolved"
        assert result.should_retry is True

    def test_keyscan_empty_output_returns_failed(self, monkeypatch, tmp_path):
        import navig.selfheal.ssh_healer as _mod

        known_hosts = tmp_path / "known_hosts"
        monkeypatch.setattr(_mod, "_KNOWN_HOSTS_PATH", known_hosts)

        proc_mock = MagicMock()
        proc_mock.returncode = 0

        async def _fake_communicate():
            return (b"", b"no keys found")

        proc_mock.communicate = _fake_communicate

        async def _fake_exec(*args, **kwargs):
            return proc_mock

        async def _fake_wait_for(coro, timeout):
            return await coro

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        healer = SSHHealer()
        result = asyncio.run(healer.keyscan_and_trust("badhost"))
        assert result.status == "failed"


class TestTcpProbe:
    def test_success(self, monkeypatch):
        writer_mock = MagicMock()
        writer_mock.close = MagicMock()

        async def _fake_open(host, port):
            return (MagicMock(), writer_mock)

        async def _fake_wait_for(coro, timeout):
            return await coro

        monkeypatch.setattr(asyncio, "open_connection", _fake_open)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        healer = SSHHealer()
        result = asyncio.run(healer._tcp_probe("goodhost", 22))
        assert result is True

    def test_connection_refused(self, monkeypatch):
        async def _fake_open(host, port):
            raise ConnectionRefusedError()

        async def _fake_wait_for(coro, timeout):
            return await coro

        monkeypatch.setattr(asyncio, "open_connection", _fake_open)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        healer = SSHHealer()
        result = asyncio.run(healer._tcp_probe("badhost", 22))
        assert result is False

    def test_timeout(self, monkeypatch):
        async def _fake_wait_for(coro, timeout):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        healer = SSHHealer()
        result = asyncio.run(healer._tcp_probe("slowhost", 22))
        assert result is False


class TestProbeSshTransport:
    def test_unreachable_returns_failed(self, monkeypatch):
        healer = SSHHealer()

        async def _fake_tcp(host, port, timeout=3.0):
            return False

        monkeypatch.setattr(healer, "_tcp_probe", _fake_tcp)
        result = asyncio.run(healer.probe_ssh_transport("deadhost", 22))
        assert result.status == "failed"
        assert "unreachable" in result.message.lower() or "TCP" in result.message

    def test_reachable_ssh_not_found(self, monkeypatch):
        healer = SSHHealer()

        async def _fake_tcp(host, port, timeout=3.0):
            return True

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(healer, "_tcp_probe", _fake_tcp)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        result = asyncio.run(healer.probe_ssh_transport("reachable", 22))
        assert result.status == "failed"
        assert "ssh" in result.message.lower()

    def test_transport_ok_auth_failure(self, monkeypatch):
        healer = SSHHealer()

        async def _fake_tcp(host, port, timeout=3.0):
            return True

        proc_mock = MagicMock()
        proc_mock.returncode = 255

        async def _fake_communicate():
            return (b"", b"debug1: Authentications that can continue: publickey")

        proc_mock.communicate = _fake_communicate

        async def _fake_exec(*args, **kwargs):
            return proc_mock

        async def _fake_wait_for(coro, timeout):
            return await coro

        monkeypatch.setattr(healer, "_tcp_probe", _fake_tcp)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)

        result = asyncio.run(healer.probe_ssh_transport("goodhost", 22))
        assert result.status == "partial"
        assert "transport" in result.message.lower()


class TestEnsureSshKey:
    def test_key_already_exists_returns_partial(self, tmp_path, monkeypatch):
        import navig.selfheal.ssh_healer as _mod

        # Create a fake key pair
        key_path = tmp_path / "id_ed25519"
        key_path.write_text("fake-private", encoding="utf-8")
        pub_path = key_path.with_suffix(".pub")
        pub_path.write_text("ssh-ed25519 AAAA mykey", encoding="utf-8")

        monkeypatch.setattr(_mod, "_DEFAULT_SSH_KEY_PATH", key_path)

        healer = SSHHealer()
        result = asyncio.run(healer.ensure_ssh_key("somehost"))
        assert result.status == "partial"
        assert result.should_retry is False

    def test_keygen_not_found(self, tmp_path, monkeypatch):
        import navig.selfheal.ssh_healer as _mod

        key_path = tmp_path / "id_ed25519_missing"
        monkeypatch.setattr(_mod, "_DEFAULT_SSH_KEY_PATH", key_path)

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        healer = SSHHealer()
        result = asyncio.run(healer.ensure_ssh_key("somehost"))
        assert result.status == "failed"
        assert "ssh-keygen" in result.message
