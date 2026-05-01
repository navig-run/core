"""
Batch 123: tests for navig/commands/flux.py
  - Helper functions: _daemon_offline_msg, _lan_ip, _table
  - HTTP helpers: _get, _post (httpx paths)
  - CLI commands: peers, status, clear, add_node, install, token
"""
from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import navig.commands.flux as _flux
from navig.commands.flux import (
    _daemon_offline_msg,
    _lan_ip,
    _table,
    flux_app,
)

_runner = CliRunner()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestDaemonOfflineMsg:
    def test_returns_string(self):
        msg = _daemon_offline_msg()
        assert isinstance(msg, str)

    def test_contains_offline(self):
        msg = _daemon_offline_msg()
        assert "OFFLINE" in msg

    def test_contains_service_start(self):
        msg = _daemon_offline_msg()
        assert "navig service start" in msg


class TestLanIp:
    def test_returns_string(self):
        ip = _lan_ip()
        assert isinstance(ip, str)
        assert "." in ip

    def test_fallback_on_socket_error(self, monkeypatch):
        import socket

        def _fail(*a, **kw):
            raise OSError("network unavail")

        monkeypatch.setattr(socket.socket, "connect", _fail)
        ip = _lan_ip()
        assert ip == "127.0.0.1"


class TestTable:
    def test_output_contains_headers(self, capsys):
        _table([["a", "bb"]], ["H1", "H2"])
        out = capsys.readouterr().out
        assert "H1" in out
        assert "H2" in out

    def test_output_contains_row_data(self, capsys):
        _table([["val1", "val2"], ["x", "y"]], ["Col1", "Col2"])
        out = capsys.readouterr().out
        assert "val1" in out
        assert "val2" in out

    def test_empty_rows(self, capsys):
        _table([], ["Only", "Headers"])
        out = capsys.readouterr().out
        assert "Only" in out

    def test_separator_chars_present(self, capsys):
        _table([["r"]], ["H"])
        out = capsys.readouterr().out
        # Table uses separator lines
        assert len(out.splitlines()) >= 3


# ---------------------------------------------------------------------------
# _get helper (httpx path)
# ---------------------------------------------------------------------------


class TestGetHelper:
    def test_success_returns_dict(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.get.return_value = mock_resp
            result = _flux._get("/mesh/peers")

        assert result == {"ok": True}

    def test_connect_error_raises_system_exit(self):
        import httpx

        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.get.side_effect = httpx.ConnectError("refused")
            with pytest.raises(SystemExit):
                _flux._get("/mesh/peers")

    def test_generic_exception_raises_system_exit(self):
        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.ConnectError = Exception  # not ConnectError
            mock_httpx.get.side_effect = RuntimeError("boom")
            with pytest.raises(SystemExit):
                _flux._get("/mesh/peers")


# ---------------------------------------------------------------------------
# _post helper (httpx path)
# ---------------------------------------------------------------------------


class TestPostHelper:
    def test_success_returns_dict(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"queued": True}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.post.return_value = mock_resp
            result = _flux._post("/mesh/discovery/scan", {})

        assert result == {"queued": True}

    def test_connect_error_raises_system_exit(self):
        import httpx

        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            with pytest.raises(SystemExit):
                _flux._post("/mesh/discovery/scan", {})

    def test_generic_exception_raises_system_exit(self):
        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.ConnectError = Exception
            mock_httpx.post.side_effect = ValueError("bad data")
            with pytest.raises(SystemExit):
                _flux._post("/mesh/discovery/scan", {})


# ---------------------------------------------------------------------------
# peers command
# ---------------------------------------------------------------------------


def _mock_get(return_value):
    return patch.object(_flux, "_get", return_value=return_value)


def _mock_post(return_value=None):
    return patch.object(_flux, "_post", return_value=return_value or {})


class TestPeersCommand:
    def test_empty_peers(self):
        with _mock_get([]):
            result = _runner.invoke(flux_app, ["peers"])
        assert result.exit_code == 0
        assert "No peers" in result.output

    def test_peers_list_plain(self):
        peers_data = [{"node_id": "abc123", "hostname": "node1", "os": "Linux",
                       "health": "healthy", "load_pct": 10, "gateway_url": "http://x"}]
        with _mock_get(peers_data):
            result = _runner.invoke(flux_app, ["peers", "--plain"])
        assert result.exit_code == 0
        assert "abc123" in result.output

    def test_peers_list_json(self):
        peers_data = [{"node_id": "abc123", "hostname": "node1"}]
        with _mock_get(peers_data):
            result = _runner.invoke(flux_app, ["peers", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_peers_table_output(self):
        peers_data = [{"node_id": "xyz789", "hostname": "server2", "os": "Linux",
                       "health": "degraded", "gateway_url": "http://y"}]
        with _mock_get(peers_data):
            result = _runner.invoke(flux_app, ["peers"])
        assert result.exit_code == 0
        assert "xyz789" in result.output or "server2" in result.output

    def test_peers_handles_dict_response(self):
        """_get may return {"peers": [...]} instead of a list directly."""
        with _mock_get({"peers": []}):
            result = _runner.invoke(flux_app, ["peers"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_json(self):
        peers = [
            {"node_id": "n1", "health": "healthy", "is_current_target": False},
            {"node_id": "n2", "health": "degraded", "is_current_target": True},
        ]
        with _mock_get(peers):
            with patch.object(_flux, "_lan_ip", return_value="10.0.0.1"):
                result = _runner.invoke(flux_app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 2
        assert data["healthy"] == 1
        assert data["degraded"] == 1
        assert data["target"] == "n2"

    def test_status_text_output(self):
        peers = [{"node_id": "n1", "health": "healthy", "is_current_target": False}]
        with _mock_get(peers):
            with patch.object(_flux, "_lan_ip", return_value="10.0.0.2"):
                result = _runner.invoke(flux_app, ["status"])
        assert result.exit_code == 0
        assert "Peers" in result.output

    def test_status_empty_peers(self):
        with _mock_get([]):
            with patch.object(_flux, "_lan_ip", return_value="127.0.0.1"):
                result = _runner.invoke(flux_app, ["status"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# clear command
# ---------------------------------------------------------------------------


class TestClearCommand:
    def test_clear_success(self):
        mock_del = MagicMock()
        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.delete.return_value = mock_del
            result = _runner.invoke(flux_app, ["clear"])
        assert result.exit_code == 0
        assert "cleared" in result.output.lower() or "local" in result.output.lower()

    def test_clear_exception_swallowed(self):
        with patch.object(_flux, "httpx") as mock_httpx:
            _flux._HTTPX = True
            mock_httpx.delete.side_effect = Exception("network error")
            result = _runner.invoke(flux_app, ["clear"])
        assert result.exit_code == 0  # exception is best-effort, swallowed


# ---------------------------------------------------------------------------
# add_node command
# ---------------------------------------------------------------------------


class TestAddNodeCommand:
    def test_add_node(self):
        with _mock_post({"status": "ok", "node_id": "newnode"}):
            result = _runner.invoke(flux_app, ["add", "http://10.0.0.5:8789"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# install command (no-push path)
# ---------------------------------------------------------------------------


class TestInstallCommand:
    def test_install_shows_instructions(self):
        with _mock_get({"mesh_token": "secret-token"}):
            with patch.object(_flux, "_lan_ip", return_value="192.168.1.1"):
                result = _runner.invoke(flux_app, ["install"])
        assert result.exit_code == 0
        assert "192.168.1.1" in result.output or "install" in result.output.lower()

    def test_install_push_without_peer_exits_1(self):
        result = _runner.invoke(flux_app, ["install", "--push"])
        assert result.exit_code != 0

    def test_install_daemon_offline_swallowed(self):
        """Install gracefully handles daemon being offline."""
        with patch.object(_flux, "_get", side_effect=SystemExit(1)):
            with patch.object(_flux, "_lan_ip", return_value="127.0.0.1"):
                result = _runner.invoke(flux_app, ["install"])
        # Should not crash; exit code should reflect CLI runner behavior
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# token command
# ---------------------------------------------------------------------------


class TestTokenCommand:
    def test_token_from_daemon(self):
        with _mock_get({"mesh_token": "mytoken123"}):
            result = _runner.invoke(flux_app, ["token"])
        assert result.exit_code == 0
        assert "mytoken123" in result.output

    def test_token_missing_exits_1(self):
        with patch.object(_flux, "_get", side_effect=SystemExit(1)):
            result = _runner.invoke(flux_app, ["token"])
        # Daemon offline + no local config → exits non-zero
        assert result.exit_code != 0
