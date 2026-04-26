"""
Tests for navig/ipc_pipe.py

Covers module constants, helper functions (_pipe_address, _is_promoted,
_promote_pipe, log_shadow_anomaly, get_pipe_status) and ShadowIPCBridge.call().
All tests are hermetic — filesystem access uses tmp_path / monkeypatching.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.ipc_pipe as ipc
from navig.ipc_pipe import (
    IPC_TIMEOUT_S,
    SHADOW_PROMOTE_AFTER,
    IPCPipeClient,
    ShadowIPCBridge,
    _pipe_address,
    get_pipe_status,
    log_shadow_anomaly,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_shadow_promote_after_is_positive_int(self):
        assert isinstance(SHADOW_PROMOTE_AFTER, int)
        assert SHADOW_PROMOTE_AFTER > 0

    def test_ipc_timeout_s_is_positive_float(self):
        assert isinstance(IPC_TIMEOUT_S, float)
        assert IPC_TIMEOUT_S > 0


# ---------------------------------------------------------------------------
# _pipe_address()
# ---------------------------------------------------------------------------


class TestPipeAddress:
    def test_returns_string(self):
        addr = _pipe_address()
        assert isinstance(addr, str) and len(addr) > 0

    def test_windows_addr_contains_pipe(self, monkeypatch):
        monkeypatch.setattr(ipc, "_IS_WINDOWS", True)
        addr = _pipe_address()
        assert "pipe" in addr.lower()

    def test_non_windows_addr_is_socket_path(self, monkeypatch):
        monkeypatch.setattr(ipc, "_IS_WINDOWS", False)
        addr = _pipe_address()
        # On Linux/macOS it's a /tmp-based socket path
        assert addr.startswith("/tmp/") or "navig" in addr


# ---------------------------------------------------------------------------
# _is_promoted() / _promote_pipe()
# ---------------------------------------------------------------------------


class TestPromotionFlag:
    def test_is_promoted_false_when_flag_absent(self, tmp_path, monkeypatch):
        flag = tmp_path / ".ipc_promoted_test"
        monkeypatch.setattr(ipc, "_PROMOTED_FLAG", flag)
        assert ipc._is_promoted() is False

    def test_is_promoted_true_when_flag_exists(self, tmp_path, monkeypatch):
        flag = tmp_path / ".ipc_promoted_test"
        flag.touch()
        monkeypatch.setattr(ipc, "_PROMOTED_FLAG", flag)
        assert ipc._is_promoted() is True

    def test_promote_pipe_creates_flag(self, tmp_path, monkeypatch):
        flag = tmp_path / ".ipc_promoted_test"
        monkeypatch.setattr(ipc, "_PROMOTED_FLAG", flag)
        ipc._promote_pipe()
        assert flag.exists()

    def test_promote_pipe_idempotent(self, tmp_path, monkeypatch):
        flag = tmp_path / ".ipc_promoted_test"
        monkeypatch.setattr(ipc, "_PROMOTED_FLAG", flag)
        ipc._promote_pipe()
        ipc._promote_pipe()  # second call should not raise
        assert flag.exists()


# ---------------------------------------------------------------------------
# log_shadow_anomaly()
# ---------------------------------------------------------------------------


class TestLogShadowAnomaly:
    def test_does_not_raise(self, tmp_path, monkeypatch):
        log_file = tmp_path / "shadow.log"
        monkeypatch.setattr(ipc, "_SHADOW_LOG", log_file)
        log_shadow_anomaly("test", "ping", {"k": "v"})

    def test_writes_json_line(self, tmp_path, monkeypatch):
        log_file = tmp_path / "shadow.log"
        monkeypatch.setattr(ipc, "_SHADOW_LOG", log_file)
        log_shadow_anomaly("test_src", "my_event", {"foo": "bar"})
        import json
        lines = [json.loads(l) for l in log_file.read_text().strip().splitlines()]
        assert len(lines) == 1
        assert lines[0]["source"] == "test_src"
        assert lines[0]["event"] == "my_event"

    def test_multiple_writes_append(self, tmp_path, monkeypatch):
        log_file = tmp_path / "shadow.log"
        monkeypatch.setattr(ipc, "_SHADOW_LOG", log_file)
        log_shadow_anomaly("a", "ea", {})
        log_shadow_anomaly("b", "eb", {})
        assert len(log_file.read_text().strip().splitlines()) == 2

    def test_unwritable_path_does_not_raise(self, tmp_path, monkeypatch):
        # Simulate IOError by pointing at a directory (can't write to it)
        monkeypatch.setattr(ipc, "_SHADOW_LOG", tmp_path)
        log_shadow_anomaly("src", "evt", {"x": 1})  # Must not raise


# ---------------------------------------------------------------------------
# get_pipe_status()
# ---------------------------------------------------------------------------


class TestGetPipeStatus:
    def test_returns_dict(self):
        result = get_pipe_status()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = get_pipe_status()
        for key in ("address", "promoted", "shadow_matches_this_session", "promote_after", "platform"):
            assert key in result, f"Missing key: {key}"

    def test_platform_matches_sys(self):
        result = get_pipe_status()
        assert result["platform"] == sys.platform

    def test_promote_after_matches_constant(self):
        result = get_pipe_status()
        assert result["promote_after"] == SHADOW_PROMOTE_AFTER


# ---------------------------------------------------------------------------
# IPCPipeClient
# ---------------------------------------------------------------------------


class TestIPCPipeClient:
    def test_default_timeout_matches_constant(self):
        client = IPCPipeClient()
        assert client.timeout == IPC_TIMEOUT_S

    def test_custom_address_stored(self):
        client = IPCPipeClient(address="custom_addr", timeout=5.0)
        assert client.address == "custom_addr"

    def test_send_returns_none_when_not_connected(self):
        # No daemon running — send should return None, not raise
        client = IPCPipeClient(address="/tmp/__navig_test_missing.sock", timeout=0.05)
        result = client.send({"cmd": "ping"})
        assert result is None


# ---------------------------------------------------------------------------
# ShadowIPCBridge.call()
# ---------------------------------------------------------------------------


class TestShadowIPCBridgeCall:
    def _bridge_with_failing_pipe(self):
        """Bridge where the pipe always fails."""
        bridge = ShadowIPCBridge()
        bridge._client.address = "/tmp/__navig_test_missing.sock"
        bridge._client.timeout = 0.05
        return bridge

    def test_falls_back_to_ws_when_pipe_fails(self):
        ws_called = []
        ws_result = {"ok": True}

        def ws_fn(payload):
            ws_called.append(payload)
            return ws_result

        bridge = self._bridge_with_failing_pipe()
        bridge._ws_send = ws_fn
        result = bridge.call({"cmd": "ping"})
        assert result == ws_result
        assert len(ws_called) == 1

    def test_returns_none_when_both_paths_fail(self):
        bridge = self._bridge_with_failing_pipe()
        # No ws_send_fn set
        result = bridge.call({"cmd": "ping"})
        assert result is None

    def test_pipe_result_returned_directly(self):
        fast_result = {"data": [1, 2, 3]}
        ws_calls = []

        bridge = ShadowIPCBridge(ws_send_fn=lambda p: ws_calls.append(p) or {"data": [1, 2, 3]})
        # Patch _client.send to succeed
        bridge._client.send = lambda payload: fast_result
        bridge._promoted = True  # skip shadow validation

        result = bridge.call({"cmd": "list"})
        assert result == fast_result

    def test_shadow_validation_spawns_thread(self):
        fast_result = {"x": 1}
        ws_result_container = []

        def slow_ws(payload):
            ws_result_container.append(payload)
            return fast_result  # matching result

        bridge = ShadowIPCBridge(ws_send_fn=slow_ws)
        bridge._client.send = lambda _: fast_result
        bridge._promoted = False  # shadow not yet promoted

        bridge.call({"cmd": "test"}, shadow=True)
        # Give thread a moment to run
        import time
        time.sleep(0.05)
        assert len(ws_result_container) >= 1

    def test_shadow_disabled_no_ws_call(self):
        ws_calls = []

        def ws_fn(p):
            ws_calls.append(p)
            return {}

        fast_result = {"r": 42}
        bridge = ShadowIPCBridge(ws_send_fn=ws_fn)
        bridge._client.send = lambda _: fast_result
        bridge._promoted = False

        bridge.call({"cmd": "x"}, shadow=False)
        assert len(ws_calls) == 0


# ---------------------------------------------------------------------------
# _shadow_match_count manipulation (module-level global)
# ---------------------------------------------------------------------------


class TestShadowMatchCounter:
    def test_shadow_validate_increments_count(self, monkeypatch):
        fast_result = {"v": 1}
        ws_result = {"v": 1}  # identical

        bridge = ShadowIPCBridge(ws_send_fn=lambda _: ws_result)
        bridge._promoted = False

        # Reset global counter to a known value
        monkeypatch.setattr(ipc, "_shadow_match_count", 0)

        bridge._shadow_validate({"cmd": "x"}, fast_result)
        assert ipc._shadow_match_count == 1

    def test_shadow_validate_resets_on_mismatch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ipc, "_SHADOW_LOG", tmp_path / "shadow.log")
        monkeypatch.setattr(ipc, "_shadow_match_count", 50)

        fast_result = {"v": 1}
        ws_result = {"v": 2}  # different

        bridge = ShadowIPCBridge(ws_send_fn=lambda _: ws_result)
        bridge._promoted = False
        bridge._shadow_validate({"cmd": "x"}, fast_result)

        assert ipc._shadow_match_count == 0
