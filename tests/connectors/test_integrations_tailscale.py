"""Tests for navig/integrations/tailscale.py"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock

import pytest

from navig.integrations.tailscale import Tailscale, TailscalePeer, TailscaleStatus


# ---------------------------------------------------------------------------
# TailscalePeer
# ---------------------------------------------------------------------------


class TestTailscalePeer:
    def test_fields(self):
        peer = TailscalePeer(
            hostname="myhost",
            tailscale_ip="100.64.0.1",
            online=True,
            os="linux",
            dns_name="myhost.ts.net",
        )
        assert peer.hostname == "myhost"
        assert peer.tailscale_ip == "100.64.0.1"
        assert peer.online is True
        assert peer.os == "linux"
        assert peer.dns_name == "myhost.ts.net"

    def test_dns_name_default_empty(self):
        peer = TailscalePeer(hostname="h", tailscale_ip="100.1.1.1", online=False, os="windows")
        assert peer.dns_name == ""


# ---------------------------------------------------------------------------
# TailscaleStatus
# ---------------------------------------------------------------------------


class TestTailscaleStatus:
    def test_to_dict_keys(self):
        status = TailscaleStatus(available=True, running=True, backend_state="Running")
        d = status.to_dict()
        assert {"available", "running", "backend_state", "self_hostname", "self_ip", "peers", "error"} <= d.keys()

    def test_to_dict_peer_included(self):
        peer = TailscalePeer(hostname="node1", tailscale_ip="100.1.1.1", online=True, os="linux")
        status = TailscaleStatus(available=True, running=True, peers=[peer])
        d = status.to_dict()
        assert len(d["peers"]) == 1
        assert d["peers"][0]["hostname"] == "node1"

    def test_error_default_empty(self):
        status = TailscaleStatus(available=False, running=False)
        assert status.error == ""


# ---------------------------------------------------------------------------
# Tailscale._run
# ---------------------------------------------------------------------------


class TestTailscaleRun:
    def test_returns_returncode_stdout_stderr(self):
        ts = Tailscale()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_result.stderr = ""
        with patch("navig.integrations.tailscale.subprocess.run", return_value=mock_result):
            rc, out, err = ts._run("status")
        assert rc == 0
        assert out == "output"
        assert err == ""

    def test_binary_not_found_returns_minus_one(self):
        ts = Tailscale()
        with patch(
            "navig.integrations.tailscale.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            rc, out, err = ts._run("status")
        assert rc == -1
        assert "not found" in err

    def test_timeout_returns_minus_one(self):
        import subprocess
        ts = Tailscale()
        with patch(
            "navig.integrations.tailscale.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["tailscale"], timeout=10),
        ):
            rc, _, err = ts._run("status")
        assert rc == -1
        assert "timed out" in err


# ---------------------------------------------------------------------------
# Tailscale.status
# ---------------------------------------------------------------------------


class TestTailscaleStatus_Method:
    def _run_async(self, coro):
        return asyncio.run(coro)

    def test_returns_unavailable_when_binary_missing(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(-1, "", "binary not found")):
            result = self._run_async(ts.status())
        assert result.available is False
        assert result.running is False
        assert "binary not found" in result.error

    def test_returns_parse_error_on_invalid_json(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(0, "not-json", "")):
            result = self._run_async(ts.status())
        assert result.available is True
        assert "JSON parse error" in result.error

    def test_running_state_detected(self):
        ts = Tailscale()
        data = {"BackendState": "Running", "Self": {}, "Peer": {}}
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert result.running is True

    def test_stopped_state_detected(self):
        ts = Tailscale()
        data = {"BackendState": "Stopped", "Self": {}, "Peer": {}}
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert result.running is False

    def test_parses_self_hostname_and_ip(self):
        ts = Tailscale()
        data = {
            "BackendState": "Running",
            "Self": {"HostName": "myhost", "TailscaleIPs": ["100.64.0.1", "fd7a::1"]},
            "Peer": {},
        }
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert result.self_hostname == "myhost"
        assert result.self_ip == "100.64.0.1"  # first IPv4

    def test_parses_peers(self):
        ts = Tailscale()
        data = {
            "BackendState": "Running",
            "Self": {},
            "Peer": {
                "peer1": {
                    "HostName": "node1",
                    "TailscaleIPs": ["100.64.0.2"],
                    "Online": True,
                    "OS": "linux",
                    "DNSName": "node1.ts.net",
                }
            },
        }
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert len(result.peers) == 1
        assert result.peers[0].hostname == "node1"
        assert result.peers[0].online is True

    def test_peer_without_online_defaults_false(self):
        ts = Tailscale()
        data = {
            "BackendState": "Running",
            "Self": {},
            "Peer": {"p1": {"HostName": "node2", "TailscaleIPs": ["100.1.1.1"]}},
        }
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert result.peers[0].online is False

    def test_empty_peer_dict_ok(self):
        ts = Tailscale()
        data = {"BackendState": "Running", "Self": {}, "Peer": None}
        with patch.object(ts, "_run", return_value=(0, json.dumps(data), "")):
            result = self._run_async(ts.status())
        assert result.peers == []


# ---------------------------------------------------------------------------
# Tailscale.ping
# ---------------------------------------------------------------------------


class TestTailscalePing:
    def _run_async(self, coro):
        return asyncio.run(coro)

    def test_returns_true_on_pong(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(0, "pong from node1", "")):
            result = self._run_async(ts.ping("node1"))
        assert result is True

    def test_returns_false_on_failure(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(1, "", "timeout")):
            result = self._run_async(ts.ping("node1"))
        assert result is False

    def test_returns_false_when_no_pong_in_output(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(0, "no response", "")):
            result = self._run_async(ts.ping("node1"))
        assert result is False


# ---------------------------------------------------------------------------
# Tailscale.ip
# ---------------------------------------------------------------------------


class TestTailscaleIp:
    def _run_async(self, coro):
        return asyncio.run(coro)

    def test_self_ip_returned(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(0, "100.64.0.1\n", "")):
            result = self._run_async(ts.ip())
        assert result == "100.64.0.1"

    def test_self_ip_returns_none_on_failure(self):
        ts = Tailscale()
        with patch.object(ts, "_run", return_value=(-1, "", "not found")):
            result = self._run_async(ts.ip())
        assert result is None

    def test_peer_ip_resolved(self):
        ts = Tailscale()
        peer = TailscalePeer(hostname="node1", tailscale_ip="100.64.0.5", online=True, os="linux")
        mock_status = TailscaleStatus(available=True, running=True, peers=[peer])
        with patch.object(ts, "status", return_value=mock_status):
            # Need to wrap the coroutine properly
            import asyncio as _asyncio

            async def _mock_status():
                return mock_status

            with patch.object(ts, "status", side_effect=_mock_status):
                result = self._run_async(ts.ip("node1"))
        assert result == "100.64.0.5"

    def test_returns_none_for_unknown_peer(self):
        ts = Tailscale()

        async def _mock_status():
            return TailscaleStatus(available=True, running=True, peers=[])

        with patch.object(ts, "status", side_effect=_mock_status):
            result = self._run_async(ts.ip("unknown-peer"))
        assert result is None

    def test_returns_none_when_tailscale_unavailable(self):
        ts = Tailscale()

        async def _mock_status():
            return TailscaleStatus(available=False, running=False)

        with patch.object(ts, "status", side_effect=_mock_status):
            result = self._run_async(ts.ip("node1"))
        assert result is None
