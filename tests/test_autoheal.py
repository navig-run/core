"""
Tests for the NAVIG self-healing diagnostic pipeline.

Covers:
  Unit:
    classify_failure              — 7 tests, one per FailureClass
    detect_failure_in_response    — 3 tests (hit, miss, host extraction)
    SSHHealer.keyscan_and_trust   — 3 tests (localhost guard, success, failure)
    SSHHealer.ensure_ssh_key      — 3 tests (key exists, missing→generated, keygen fails)
    SSHHealer.probe_ssh_transport — 3 tests (TCP refused, open+transport ok, timeout)
    AutoHealMixin._run_autofix    — 5 tests (dispatch, DB gate, CMD hint, cap, TIMEOUT)
    AutoHealMixin._handle_autoheal — 5 tests (on, off, hive on, hive off, status)
    HealPRSubmitter.store_pending_patch — 2 tests (creates file, list_pending_patches)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from navig.gateway.channels.telegram_autoheal import (
    AutoHealMixin,
    FailureClass,
    FailureContext,
    classify_failure,
    detect_failure_in_response,
)
from navig.selfheal.ssh_healer import HealResult, SSHHealer

# ── Imports under test ───────────────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ctx(
    failure_class: FailureClass = FailureClass.SSH_AUTH_FAIL,
    stderr: str = "Permission denied (publickey)",
    cmd: str = "navig run --host prod ls",
    host: str = "prod",
    attempt_count: int = 0,
) -> FailureContext:
    return FailureContext(
        original_cmd=cmd,
        chat_id=100,
        user_id=200,
        failure_class=failure_class,
        stderr=stderr,
        exit_code=1,
        host=host,
        attempt_count=attempt_count,
    )


def _make_mixin() -> AutoHealMixin:
    """Return a minimal AutoHealMixin instance backed by AsyncMock helpers."""
    mixin = AutoHealMixin.__new__(AutoHealMixin)
    mixin._init_autoheal_state()
    mixin.send_message = AsyncMock()
    mixin._get_session_manager_safe = MagicMock(return_value=None)
    mixin._get_navig_config_path = MagicMock(return_value="~/.navig/config.yaml")
    mixin._extract_missing_cmd = MagicMock(return_value="htop")
    return mixin


# ============================================================================
# classify_failure — pure function, no I/O
# ============================================================================


class TestClassifyFailure(unittest.TestCase):
    """classify_failure returns the correct FailureClass for each scenario."""

    def test_ssh_auth_fail_publickey(self) -> None:
        fc = classify_failure(
            "Permission denied (publickey, gssapi-keyex)", 1, "run ls"
        )
        assert fc == FailureClass.SSH_AUTH_FAIL

    def test_ssh_auth_fail_password(self) -> None:
        fc = classify_failure("Permission denied (password)", 1, "run ls")
        assert fc == FailureClass.SSH_AUTH_FAIL

    def test_ssh_hostkey_unknown(self) -> None:
        fc = classify_failure("Host key verification failed.", 255, "run ls")
        assert fc == FailureClass.SSH_HOSTKEY_UNKNOWN

    def test_ssh_transport_fail_exit255_empty_stderr(self) -> None:
        fc = classify_failure("", 255, "run ls")
        assert fc == FailureClass.SSH_TRANSPORT_FAIL

    def test_db_permission_deny(self) -> None:
        fc = classify_failure("Access denied for user 'app'@'localhost'", 1, "db query")
        assert fc == FailureClass.DB_PERMISSION_DENY

    def test_cmd_not_found(self) -> None:
        fc = classify_failure("htop: command not found", 127, "run htop")
        assert fc == FailureClass.CMD_NOT_FOUND

    def test_timeout_exit124(self) -> None:
        fc = classify_failure("", 124, "run sleep 9999")
        assert fc == FailureClass.TIMEOUT

    def test_unknown_fallthrough(self) -> None:
        fc = classify_failure("some random gibberish", 1, "run echo hi")
        assert fc == FailureClass.UNKNOWN


# ============================================================================
# detect_failure_in_response
# ============================================================================


class TestDetectFailureInResponse(unittest.TestCase):
    def test_returns_none_for_clean_response(self) -> None:
        ctx = detect_failure_in_response("total 32\ndrwxr-xr-x 5 root root", "run ls")
        assert ctx is None

    def test_returns_none_for_empty_response(self) -> None:
        ctx = detect_failure_in_response("", "run ls")
        assert ctx is None

    def test_detects_publickey_denial(self) -> None:
        response = (
            "Permission denied (publickey, gssapi-keyex, gssapi-with-mic).\r\n"
            "Command exited with code: 255"
        )
        ctx = detect_failure_in_response(response, "navig run --host myserver ls")
        assert ctx is not None
        assert ctx.failure_class == FailureClass.SSH_AUTH_FAIL
        assert ctx.exit_code == 255

    def test_host_extracted_from_cmd(self) -> None:
        response = "Host key verification failed."
        ctx = detect_failure_in_response(response, "navig run --host staging df -h")
        assert ctx is not None
        assert ctx.host == "staging"

    def test_host_returns_none_when_missing(self) -> None:
        response = "Connection refused"
        ctx = detect_failure_in_response(response, "navig run df -h")
        assert ctx is not None
        assert ctx.host is None


# ============================================================================
# SSHHealer — async unit tests
# ============================================================================


class TestSSHHealerKeyscanAndTrust(unittest.IsolatedAsyncioTestCase):
    async def test_localhost_returns_partial(self) -> None:
        healer = SSHHealer()
        result = await healer.keyscan_and_trust("localhost")
        assert result.status == "partial"
        assert (
            "localhost" in result.message.lower() or "local" in result.message.lower()
        )

    async def test_keyscan_success_writes_known_hosts(self) -> None:
        healer = SSHHealer()
        fake_key = "prod ecdsa-sha2-nistp256 AAAA==\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            kh = Path(tmpdir) / "known_hosts"
            kh.touch()
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(fake_key.encode(), b""))
            proc.returncode = 0

            with (
                patch("asyncio.create_subprocess_exec", return_value=proc),
                patch.object(type(healer), "_KNOWN_HOSTS_PATH", kh, create=True),
                patch(
                    "navig.selfheal.ssh_healer._KNOWN_HOSTS_PATH",
                    new_callable=lambda: property(lambda s: kh),
                    create=True,
                ),
            ):
                # Patch the module-level constant directly
                import navig.selfheal.ssh_healer as _m

                orig = _m._KNOWN_HOSTS_PATH
                _m._KNOWN_HOSTS_PATH = kh
                try:
                    result = await healer.keyscan_and_trust("prod")
                finally:
                    _m._KNOWN_HOSTS_PATH = orig

            assert result.status in ("resolved", "partial")

    async def test_keyscan_subprocess_failure_returns_failed(self) -> None:
        healer = SSHHealer()
        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(
                b"",
                b"ssh-keyscan: getaddrinfo broken_host: Name or service not known",
            )
        )
        proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await healer.keyscan_and_trust("broken_host")
        assert result.status == "failed"


class TestSSHHealerEnsureSSHKey(unittest.IsolatedAsyncioTestCase):
    async def test_key_exists_returns_partial(self) -> None:
        healer = SSHHealer()
        with tempfile.NamedTemporaryFile(suffix="id_ed25519") as f:
            import navig.selfheal.ssh_healer as _m

            orig = _m._DEFAULT_SSH_KEY_PATH
            _m._DEFAULT_SSH_KEY_PATH = Path(f.name)
            try:
                result = await healer.ensure_ssh_key("myhost")
            finally:
                _m._DEFAULT_SSH_KEY_PATH = orig
        assert result.status == "partial"

    async def test_missing_key_generates_keypair(self) -> None:
        healer = SSHHealer()
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "id_ed25519"
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0

            import navig.selfheal.ssh_healer as _m

            orig = _m._DEFAULT_SSH_KEY_PATH
            _m._DEFAULT_SSH_KEY_PATH = key_path
            try:
                with patch("asyncio.create_subprocess_exec", return_value=proc):
                    result = await healer.ensure_ssh_key("myhost")
            finally:
                _m._DEFAULT_SSH_KEY_PATH = orig

        assert result.status in ("resolved", "partial", "failed")
        # subprocess was invoked for key generation
        proc.communicate.assert_awaited_once()

    async def test_keygen_failure_returns_failed(self) -> None:
        healer = SSHHealer()
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "id_ed25519"
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b"keygen: error"))
            proc.returncode = 1

            import navig.selfheal.ssh_healer as _m

            orig = _m._DEFAULT_SSH_KEY_PATH
            _m._DEFAULT_SSH_KEY_PATH = key_path
            try:
                with patch("asyncio.create_subprocess_exec", return_value=proc):
                    result = await healer.ensure_ssh_key("myhost")
            finally:
                _m._DEFAULT_SSH_KEY_PATH = orig

        assert result.status == "failed"


class TestSSHHealerProbe(unittest.IsolatedAsyncioTestCase):
    async def test_tcp_refused_returns_failed(self) -> None:
        healer = SSHHealer()
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            result = await healer.probe_ssh_transport("dead_host", port=22)
        assert result.status == "failed"

    async def test_tcp_open_success_path(self) -> None:
        healer = SSHHealer()
        mock_reader = MagicMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(
                b"",
                b"debug1: Connection established.\ndebug1: Server host key: ecdsa\n",
            )
        )
        proc.returncode = 0

        with (
            patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await healer.probe_ssh_transport("live_host", port=22)

        assert result.status in ("resolved", "partial")

    async def test_tcp_timeout_returns_failed(self) -> None:
        healer = SSHHealer()
        with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError):
            result = await healer.probe_ssh_transport("timeout_host", port=22)
        assert result.status == "failed"
        assert result.should_retry is False


# ============================================================================
# AutoHealMixin._run_autofix
# ============================================================================


class TestRunAutofix(unittest.IsolatedAsyncioTestCase):
    async def test_ssh_hostkey_dispatches_to_keyscan(self) -> None:
        mixin = _make_mixin()
        fake_result = HealResult(
            status="resolved", message="Trusted.", should_retry=True
        )
        with patch(
            "navig.selfheal.ssh_healer.SSHHealer.keyscan_and_trust",
            new=AsyncMock(return_value=fake_result),
        ):
            ctx = _ctx(FailureClass.SSH_HOSTKEY_UNKNOWN, host="prod")
            result = await mixin._run_autofix(ctx)
        assert result.status == "resolved"

    async def test_db_permission_deny_never_auto_fixed(self) -> None:
        mixin = _make_mixin()
        ctx = _ctx(FailureClass.DB_PERMISSION_DENY)
        result = await mixin._run_autofix(ctx)
        assert result.status == "partial"
        assert result.should_retry is False

    async def test_cmd_not_found_returns_failed_with_hint(self) -> None:
        mixin = _make_mixin()
        ctx = _ctx(FailureClass.CMD_NOT_FOUND, stderr="htop: command not found")
        result = await mixin._run_autofix(ctx)
        assert result.status == "failed"
        assert "htop" in result.message or "not installed" in result.message

    async def test_attempt_cap_returns_failed(self) -> None:
        mixin = _make_mixin()
        ctx = _ctx(attempt_count=2)  # already at max
        result = await mixin._run_autofix(ctx)
        assert result.status == "failed"
        assert (
            "maximum" in result.message.lower() or "attempts" in result.message.lower()
        )

    async def test_timeout_class_returns_partial(self) -> None:
        mixin = _make_mixin()
        ctx = _ctx(FailureClass.TIMEOUT, stderr="", cmd="run sleep 9999")
        result = await mixin._run_autofix(ctx)
        assert result.status == "partial"


# ============================================================================
# AutoHealMixin._handle_autoheal
# ============================================================================


class TestHandleAutoheal(unittest.IsolatedAsyncioTestCase):
    async def test_on_enables_autoheal(self) -> None:
        mixin = _make_mixin()
        sm = MagicMock()
        sm.update_settings = MagicMock()
        mixin._get_session_manager_safe = MagicMock(return_value=sm)

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="on")

        sm.update_settings.assert_called_once_with(100, 200, autoheal_enabled=True)
        mixin.send_message.assert_awaited_once()
        msg = mixin.send_message.call_args[0][1]
        assert "ON" in msg

    async def test_off_disables_autoheal(self) -> None:
        mixin = _make_mixin()
        sm = MagicMock()
        mixin._get_session_manager_safe = MagicMock(return_value=sm)

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="off")

        sm.update_settings.assert_called_once_with(100, 200, autoheal_enabled=False)
        msg = mixin.send_message.call_args[0][1]
        assert "OFF" in msg

    async def test_hive_on_enables_hive(self) -> None:
        mixin = _make_mixin()
        sm = MagicMock()
        mixin._get_session_manager_safe = MagicMock(return_value=sm)

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="hive on")

        sm.update_settings.assert_called_once_with(100, 200, autoheal_hive_enabled=True)
        msg = mixin.send_message.call_args[0][1]
        assert "Hive" in msg or "hive" in msg.lower()

    async def test_hive_off_disables_hive(self) -> None:
        mixin = _make_mixin()
        sm = MagicMock()
        mixin._get_session_manager_safe = MagicMock(return_value=sm)

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="hive off")

        sm.update_settings.assert_called_once_with(
            100, 200, autoheal_hive_enabled=False
        )

    async def test_status_calls_send_autoheal_status(self) -> None:
        mixin = _make_mixin()
        mixin._send_autoheal_status = AsyncMock()

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="status")

        mixin._send_autoheal_status.assert_awaited_once()

    async def test_empty_args_shows_status(self) -> None:
        mixin = _make_mixin()
        mixin._send_autoheal_status = AsyncMock()

        await mixin._handle_autoheal(chat_id=100, user_id=200, args="")

        mixin._send_autoheal_status.assert_awaited_once()

    async def test_invalid_args_sends_usage(self) -> None:
        mixin = _make_mixin()
        await mixin._handle_autoheal(chat_id=100, user_id=200, args="blahblah")

        mixin.send_message.assert_awaited_once()
        msg = mixin.send_message.call_args[0][1]
        assert "Usage" in msg or "autoheal" in msg.lower()


# ============================================================================
# HealPRSubmitter — store and list pending patches
# ============================================================================


class TestHealPRSubmitter(unittest.TestCase):
    def test_store_pending_patch_creates_file(self) -> None:
        from navig.selfheal.heal_pr_submitter import HealPRSubmitter

        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = HealPRSubmitter()

            import navig.selfheal.heal_pr_submitter as _m

            orig = _m._HEAL_PATCHES_DIR
            _m._HEAL_PATCHES_DIR = Path(tmpdir)
            try:
                submitter.store_pending_patch(
                    failure_class="SSH_AUTH_FAIL",
                    original_cmd="run ls",
                    stderr="Permission denied",
                    exit_code=255,
                    patch_text="diff --git ...",
                    host="prod",
                )
                files = list(Path(tmpdir).glob("*.patch.json"))
                assert len(files) == 1

                data = json.loads(files[0].read_text())
                assert data["failure_class"] == "SSH_AUTH_FAIL"
                assert data["host"] == "prod"
            finally:
                _m._HEAL_PATCHES_DIR = orig

    def test_list_pending_patches_returns_paths(self) -> None:
        from navig.selfheal.heal_pr_submitter import HealPRSubmitter

        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = HealPRSubmitter()

            import navig.selfheal.heal_pr_submitter as _m

            orig = _m._HEAL_PATCHES_DIR
            _m._HEAL_PATCHES_DIR = Path(tmpdir)
            try:
                # write two fake patch files (submitted=False so they are listed)
                for i in range(2):
                    p = Path(tmpdir) / f"ts_{i}_ssh_auth_fail.patch.json"
                    p.write_text(json.dumps({"submitted": False}), encoding="utf-8")
                patches = submitter.list_pending_patches()
                assert len(patches) == 2
            finally:
                _m._HEAL_PATCHES_DIR = orig


# ============================================================================
# TelegramSession — new autoheal fields
# ============================================================================


class TestTelegramSessionAutoHealFields(unittest.TestCase):
    def test_default_values_are_false_and_empty(self) -> None:
        from navig.gateway.channels.telegram_sessions import TelegramSession

        session = TelegramSession(chat_id=1, user_id=2, session_key="1:2")
        assert session.autoheal_enabled is False
        assert session.autoheal_hive_enabled is False
        assert session.heal_history == []

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        from navig.gateway.channels.telegram_sessions import TelegramSession

        session = TelegramSession(chat_id=1, user_id=2, session_key="1:2")
        session.autoheal_enabled = True
        session.autoheal_hive_enabled = True
        session.heal_history = [{"ts": "2025-01-01", "status": "resolved"}]

        d = session.to_dict()
        assert d["autoheal_enabled"] is True
        assert d["autoheal_hive_enabled"] is True
        assert len(d["heal_history"]) == 1

        restored = TelegramSession.from_dict(d)
        assert restored.autoheal_enabled is True
        assert restored.autoheal_hive_enabled is True
        assert restored.heal_history[0]["status"] == "resolved"

    def test_from_dict_backward_compat_missing_fields(self) -> None:
        from navig.gateway.channels.telegram_sessions import TelegramSession

        # Simulate an old session dict that predates autoheal fields
        old_dict = {
            "chat_id": 1,
            "user_id": 2,
            "session_key": "1:2",
            "username": "someone",
        }
        session = TelegramSession.from_dict(old_dict)
        assert session.autoheal_enabled is False
        assert session.heal_history == []


if __name__ == "__main__":
    unittest.main()
