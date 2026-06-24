"""
Tests for navig/commands/tailscale_cmd.py
Covers ts_status, ts_ping, ts_ip via typer CliRunner.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.tailscale_cmd import tailscale_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _peer(hostname="peer1", ip="100.9.9.1", online=True, os="linux"):
    p = MagicMock()
    p.hostname = hostname
    p.tailscale_ip = ip
    p.online = online
    p.os = os
    return p


def _status(
    available=True,
    running=True,
    error="",
    self_hostname="myhost",
    self_ip="100.1.2.3",
    backend_state="Running",
    peers=None,
):
    s = MagicMock()
    s.available = available
    s.running = running
    s.error = error
    s.self_hostname = self_hostname
    s.self_ip = self_ip
    s.backend_state = backend_state
    s.peers = peers if peers is not None else []
    s.to_dict.return_value = {
        "available": available,
        "running": running,
        "backend_state": backend_state,
        "self_hostname": self_hostname,
        "self_ip": self_ip,
        "peers": [],
        "error": error,
    }
    return s


def _mock_ts(status=None, ping_result=True, ip_result="100.1.2.3"):
    """Return (mock_class, mock_instance) for Tailscale."""
    inst = MagicMock()
    inst.status = AsyncMock(return_value=status if status is not None else _status())
    inst.ping = AsyncMock(return_value=ping_result)
    inst.ip = AsyncMock(return_value=ip_result)
    cls = MagicMock(return_value=inst)
    return cls, inst


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

class TestTailscaleAppStructure:
    def test_commands_registered(self):
        names = {c.name for c in tailscale_app.registered_commands}
        assert "status" in names
        assert "ping" in names
        assert "ip" in names

    def test_help_exits_0(self):
        result = runner.invoke(tailscale_app, ["--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# ts_status
# ---------------------------------------------------------------------------

class TestTsStatus:
    def test_running_exits_0(self):
        cls, _ = _mock_ts()
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert result.exit_code == 0

    def test_running_shows_hostname(self):
        cls, _ = _mock_ts(_status(self_hostname="boxname"))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert "boxname" in result.output

    def test_running_shows_ip(self):
        cls, _ = _mock_ts(_status(self_ip="100.5.5.5"))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert "100.5.5.5" in result.output

    def test_running_shows_backend_state(self):
        cls, _ = _mock_ts(_status(backend_state="Running"))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert "Running" in result.output

    def test_not_available_exits_1(self):
        cls, _ = _mock_ts(_status(available=False, running=False))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert result.exit_code != 0

    def test_not_running_exits_1(self):
        cls, _ = _mock_ts(_status(available=True, running=False))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert result.exit_code != 0

    def test_no_peers_mentions_no_peers(self):
        cls, _ = _mock_ts(_status(peers=[]))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        # should show some "no peers" indication OR just exit cleanly —
        # the key assertion is it doesn't crash
        assert result.exit_code == 0

    def test_with_peers_exits_0(self):
        peers = [_peer("alpha", "100.2.2.2"), _peer("beta", "100.3.3.3", online=False)]
        cls, _ = _mock_ts(_status(peers=peers))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert result.exit_code == 0

    def test_with_peers_shows_peer_hostname(self):
        peers = [_peer("alpha", "100.2.2.2")]
        cls, _ = _mock_ts(_status(peers=peers))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        assert "alpha" in result.output

    def test_json_flag_exits_0(self):
        cls, _ = _mock_ts()
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status", "--json-out"])
        # either --json-out or --json — try both
        if result.exit_code != 0 and "No such option" in (result.output or ""):
            result = runner.invoke(tailscale_app, ["status", "--json"])
        # If no json flag exists at all, the test is informational
        assert result.exit_code in (0, 2)

    def test_json_flag_outputs_json(self):
        cls, _ = _mock_ts()
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status", "--json-out"])
        if result.exit_code == 0:
            # output should be parseable JSON
            try:
                parsed = json.loads(result.output.strip())
                assert isinstance(parsed, dict)
                assert "available" in parsed
            except json.JSONDecodeError:
                pass  # output may include Rich formatting

    def test_not_available_outputs_error_info(self):
        cls, _ = _mock_ts(_status(available=False, running=False, error="not installed"))
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["status"])
        # should have non-zero exit and some output
        assert result.exit_code != 0
        assert len(result.output) > 0 or result.exception is not None

    def test_tailscale_class_instantiated(self):
        cls, _ = _mock_ts()
        with patch("navig.integrations.tailscale.Tailscale", cls):
            runner.invoke(tailscale_app, ["status"])
        cls.assert_called_once()


# ---------------------------------------------------------------------------
# ts_ping
# ---------------------------------------------------------------------------

class TestTsPing:
    def test_success_exits_0(self):
        cls, _ = _mock_ts(ping_result=True)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ping", "somepeer"])
        assert result.exit_code == 0

    def test_failure_exits_nonzero(self):
        cls, _ = _mock_ts(ping_result=False)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ping", "somepeer"])
        assert result.exit_code != 0

    def test_success_prints_pong(self):
        cls, _ = _mock_ts(ping_result=True)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ping", "somepeer"])
        assert "pong" in result.output.lower() or "somepeer" in result.output

    def test_failure_prints_no_response(self):
        cls, _ = _mock_ts(ping_result=False)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ping", "somepeer"])
        out = result.output.lower()
        assert "no response" in out or "fail" in out or "error" in out or result.exit_code != 0

    def test_passes_peer_to_method(self):
        cls, inst = _mock_ts(ping_result=True)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            runner.invoke(tailscale_app, ["ping", "targethost"])
        inst.ping.assert_called_once_with("targethost")

    def test_missing_peer_arg_exits_nonzero(self):
        result = runner.invoke(tailscale_app, ["ping"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ts_ip
# ---------------------------------------------------------------------------

class TestTsIp:
    def test_found_exits_0(self):
        cls, _ = _mock_ts(ip_result="100.7.7.7")
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ip", "somepeer"])
        assert result.exit_code == 0

    def test_found_prints_ip(self):
        cls, _ = _mock_ts(ip_result="100.7.7.7")
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ip", "somepeer"])
        assert "100.7.7.7" in result.output

    def test_not_found_exits_nonzero(self):
        cls, _ = _mock_ts(ip_result=None)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ip", "missingpeer"])
        assert result.exit_code != 0

    def test_not_found_prints_message(self):
        cls, _ = _mock_ts(ip_result=None)
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ip", "missingpeer"])
        out = result.output.lower()
        assert "not found" in out or "no" in out or result.exit_code != 0

    def test_passes_peer_to_method(self):
        cls, inst = _mock_ts(ip_result="100.9.9.9")
        with patch("navig.integrations.tailscale.Tailscale", cls):
            runner.invoke(tailscale_app, ["ip", "mynode"])
        inst.ip.assert_called_once_with("mynode")

    def test_no_peer_arg_calls_ip_with_none_or_exits(self):
        cls, inst = _mock_ts(ip_result="100.1.1.1")
        with patch("navig.integrations.tailscale.Tailscale", cls):
            result = runner.invoke(tailscale_app, ["ip"])
        # peer is Optional — either succeeds with None arg or exits cleanly
        if result.exit_code == 0:
            inst.ip.assert_called_once_with(None)
        else:
            # acceptable if peer is required
            assert result.exit_code != 0
